"""backend/src/handlers/routines.py 端點測試：授權、冪等、版本推進與 occurrence 推導。"""
from datetime import datetime
import json

import pytest

from src.handlers import routines as handler
from src.shared import db, routines as domain

ELDER_ID = "eld_001"
CAREGIVER_SUB = "usr_care"
ELDER_SUB = "usr_elder"
NOW = datetime.fromisoformat("2026-07-14T10:00:00+08:00")


# -----------------------------------------------------------------------------
# 測試替身
# -----------------------------------------------------------------------------

def _event(method, *, body=None, params=None, routine_id=None, claims=None, resource="/routines"):
    """組出 API Gateway 事件；預設呼叫者為照護者。"""
    return {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": {"routine_id": routine_id} if routine_id else None,
        "queryStringParameters": params,
        "body": json.dumps(body, ensure_ascii=False) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims or {"sub": CAREGIVER_SUB}}},
    }


def _elder_claims(elder_id=ELDER_ID):
    return {"sub": ELDER_SUB, "elder_id": elder_id}


def _body(response):
    return json.loads(response["body"])


def _code(response):
    return response["statusCode"], _body(response)["error"]["code"]


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    """固定「現在」為 2026-07-14 10:00，讓 occurrence 狀態可預期。"""
    monkeypatch.setattr(domain, "now", lambda: NOW)


@pytest.fixture(autouse=True)
def bound_caregiver(monkeypatch):
    """照護者已綁定 ELDER_ID；其他長者一律查無。"""
    monkeypatch.setattr(
        db,
        "get_elder",
        lambda elder_id: (
            {"elder_id": elder_id, "caregiver_ids": [CAREGIVER_SUB]}
            if elder_id == ELDER_ID
            else None
        ),
    )


@pytest.fixture
def store(monkeypatch):
    """以記憶體替換 DynamoDB 存取，並保留條件式寫入的行為。"""
    state = {"versions": [], "events": {}, "next_token": None}

    def get_routine_version(routine_id, version):
        return next(
            (
                v
                for v in state["versions"]
                if v["routine_id"] == routine_id and int(v["version"]) == int(version)
            ),
            None,
        )

    def list_routine_versions(routine_id):
        return sorted(
            (v for v in state["versions"] if v["routine_id"] == routine_id),
            key=lambda v: int(v["version"]),
        )

    def put_routine_version(item):
        if get_routine_version(item["routine_id"], item["version"]):
            raise db.ConditionFailedError("例行公事版本已存在")
        state["versions"].append(item)
        return item

    def replace_current_routine_version(current, next_version):
        stored = get_routine_version(current["routine_id"], current["version"])
        if stored is None or not stored.get("is_current"):
            raise db.ConditionFailedError("例行公事已被其他請求改版")
        stored["is_current"] = False
        stored["effective_to"] = next_version["effective_from"]
        stored.pop("current_sort_key", None)
        state["versions"].append(next_version)
        return next_version

    def list_current_routines(elder_id, active_only=True, limit=50, next_token=None):
        items = [
            v
            for v in state["versions"]
            if v["elder_id"] == elder_id and v.get("is_current")
        ]
        if active_only:
            items = [v for v in items if v.get("active", True)]
        items.sort(key=lambda v: v["current_sort_key"])
        return items[:limit], state["next_token"]

    def list_routine_versions_by_elder(elder_id, upper_bound):
        return [
            v
            for v in state["versions"]
            if v["elder_id"] == elder_id and v["version_time_key"] <= upper_bound
        ]

    def put_event_if_absent(event_data):
        item = dict(event_data)
        item["event_id"] = db.event_id_for(item["elder_id"], item["canonical_event_key"])
        existing = state["events"].get(item["event_id"])
        if existing:
            return existing, False
        state["events"][item["event_id"]] = item
        return item, True

    def get_events(elder_id, event_ids):
        return {eid: state["events"][eid] for eid in event_ids if eid in state["events"]}

    for name, fake in {
        "get_routine_version": get_routine_version,
        "list_routine_versions": list_routine_versions,
        "put_routine_version": put_routine_version,
        "replace_current_routine_version": replace_current_routine_version,
        "list_current_routines": list_current_routines,
        "list_routine_versions_by_elder": list_routine_versions_by_elder,
        "put_event_if_absent": put_event_if_absent,
        "get_events": get_events,
    }.items():
        monkeypatch.setattr(db, name, fake)

    return state


def _seed_routine(store, routine_id="rtn_001", **overrides):
    """預置一筆 current 版本。"""
    created_at = "2026-07-01T10:00:00.000+08:00"
    item = {
        "routine_id": routine_id,
        "version": 1,
        "elder_id": ELDER_ID,
        "is_current": True,
        "active": True,
        "remind": True,
        "title": "吃血壓藥",
        "type": "medication",
        "schedule": {"freq": "daily", "time": "09:00"},
        "effective_from": created_at,
        "current_sort_key": domain.current_sort_key(True, created_at, routine_id),
        "version_time_key": domain.version_time_key(created_at, routine_id, 1),
        "created_by": "caregiver",
        "created_by_id": CAREGIVER_SUB,
        "updated_by": "caregiver",
        "updated_by_id": CAREGIVER_SUB,
        "change_request_id": "chg_seed",
        "request_hash": "seed",
        "created_at": created_at,
        "updated_at": created_at,
        "schema_version": 1,
    }
    item.update(overrides)
    store["versions"].append(item)
    return item


# -----------------------------------------------------------------------------
# GET /routines
# -----------------------------------------------------------------------------

def test_get_definitions_returns_public_fields_only(store):
    _seed_routine(store)
    resp = handler.handler(_event("GET", params={"elder_id": ELDER_ID}), None)

    assert resp["statusCode"] == 200
    items = _body(resp)["items"]
    assert len(items) == 1
    assert items[0] == {
        "routine_id": "rtn_001",
        "elder_id": ELDER_ID,
        "title": "吃血壓藥",
        "type": "medication",
        "schedule": {"freq": "daily", "time": "09:00"},
        "remind": True,
        "active": True,
        "created_by": "caregiver",
        "created_at": "2026-07-01T10:00:00.000+08:00",
    }


def test_get_definitions_paginates_with_next_token(store):
    _seed_routine(store)
    store["next_token"] = "eyJfIjoxfQ=="
    resp = handler.handler(_event("GET", params={"elder_id": ELDER_ID}), None)
    assert _body(resp)["next_token"] == "eyJfIjoxfQ=="


def test_get_requires_elder_id(store):
    assert _code(handler.handler(_event("GET"), None)) == (400, "INVALID_PARAMETER")


def test_get_rejects_bad_date(store):
    resp = handler.handler(_event("GET", params={"elder_id": ELDER_ID, "date": "07/14"}), None)
    assert _code(resp) == (400, "INVALID_PARAMETER")


def test_get_rejects_bad_limit(store):
    resp = handler.handler(_event("GET", params={"elder_id": ELDER_ID, "limit": "0"}), None)
    assert _code(resp) == (400, "INVALID_PARAMETER")


def test_elder_cannot_read_other_elder(store):
    resp = handler.handler(
        _event("GET", params={"elder_id": ELDER_ID}, claims=_elder_claims("eld_999")), None
    )
    assert _code(resp) == (403, "FORBIDDEN")


def test_get_daily_view_derives_status(store):
    _seed_routine(store, routine_id="rtn_001")  # 09:00，10:00 查詢仍在寬限期內
    _seed_routine(
        store,
        routine_id="rtn_002",
        title="量血壓",
        type="other",
        schedule={"freq": "daily", "time": "19:00"},
    )
    # rtn_001 已由對話完成
    completion_key = domain.completion_event_key("rtn_001", "2026-07-14")
    event_id = db.event_id_for(ELDER_ID, completion_key)
    store["events"][event_id] = {
        "event_id": event_id,
        "elder_id": ELDER_ID,
        "routine_id": "rtn_001",
        "routine_version": 1,
        "routine_date": "2026-07-14",
        "ts": "2026-07-14T09:05:00.000+08:00",
        "completed_by": "conversation",
    }

    resp = handler.handler(
        _event("GET", params={"elder_id": ELDER_ID, "date": "2026-07-14"}), None
    )
    body = _body(resp)

    assert resp["statusCode"] == 200
    assert body["date"] == "2026-07-14"
    assert body["items"] == [
        {
            "routine_id": "rtn_001",
            "title": "吃血壓藥",
            "type": "medication",
            "scheduled_at": "2026-07-14T09:00:00+08:00",
            "status": "done",
            "completed_at": "2026-07-14T09:05:00.000+08:00",
            "completed_by": "conversation",
        },
        {
            "routine_id": "rtn_002",
            "title": "量血壓",
            "type": "other",
            "scheduled_at": "2026-07-14T19:00:00+08:00",
            "status": "pending",
        },
    ]


# -----------------------------------------------------------------------------
# POST /routines
# -----------------------------------------------------------------------------

CREATE_BODY = {
    "client_request_id": "req-1",
    "elder_id": ELDER_ID,
    "title": "吃血壓藥",
    "type": "medication",
    "schedule": {"freq": "daily", "time": "09:00"},
    "remind": True,
}


def test_create_returns_201_and_stores_first_version(store):
    resp = handler.handler(_event("POST", body=CREATE_BODY), None)
    body = _body(resp)

    assert resp["statusCode"] == 201
    assert body["routine_id"].startswith("rtn_")
    assert body["active"] is True
    assert body["created_by"] == "caregiver"
    # 內部欄位不外露
    assert not {"version", "is_current", "request_hash", "change_request_id"} & body.keys()

    stored = store["versions"][0]
    assert stored["version"] == 1
    assert stored["is_current"] is True
    assert stored["current_sort_key"].startswith("A#")
    assert stored["created_by_id"] == CAREGIVER_SUB


def test_create_replays_same_request(store):
    first = _body(handler.handler(_event("POST", body=CREATE_BODY), None))
    second = handler.handler(_event("POST", body=CREATE_BODY), None)

    assert second["statusCode"] == 201
    assert _body(second) == first
    assert len(store["versions"]) == 1


def test_create_conflicts_on_same_request_id_different_payload(store):
    handler.handler(_event("POST", body=CREATE_BODY), None)
    resp = handler.handler(_event("POST", body={**CREATE_BODY, "title": "換成量血壓"}), None)

    assert _code(resp) == (409, "IDEMPOTENCY_CONFLICT")
    assert len(store["versions"]) == 1


def test_create_rejected_for_elder_caller(store):
    resp = handler.handler(_event("POST", body=CREATE_BODY, claims=_elder_claims()), None)
    assert _code(resp) == (403, "FORBIDDEN")


@pytest.mark.parametrize(
    "body",
    [
        {**CREATE_BODY, "schedule": {"freq": "weekly", "time": "09:00"}},  # 缺 weekday
        {**CREATE_BODY, "schedule": {"freq": "daily", "time": "9am"}},  # 時間格式錯
        {**CREATE_BODY, "active": True},  # server-owned 欄位
        {k: v for k, v in CREATE_BODY.items() if k != "client_request_id"},  # 缺冪等鍵
    ],
)
def test_create_rejects_invalid_body(store, body):
    assert _code(handler.handler(_event("POST", body=body), None)) == (
        400,
        "INVALID_PARAMETER",
    )


def test_create_rejects_unbound_elder(store):
    resp = handler.handler(_event("POST", body={**CREATE_BODY, "elder_id": "eld_999"}), None)
    assert _code(resp) == (404, "ELDER_NOT_FOUND")


# -----------------------------------------------------------------------------
# PATCH /routines/{routine_id}
# -----------------------------------------------------------------------------

def test_patch_creates_next_version_and_closes_current(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "schedule": {"freq": "daily", "time": "08:30"}},
            resource="/routines/{routine_id}",
        ),
        None,
    )

    assert resp["statusCode"] == 200
    assert _body(resp)["schedule"] == {"freq": "daily", "time": "08:30"}

    old, new = store["versions"]
    assert old["is_current"] is False
    assert "current_sort_key" not in old
    assert old["effective_to"] == new["effective_from"]
    assert new["version"] == 2
    assert new["is_current"] is True
    # routine_id 與建立資料不因改版而變
    assert new["routine_id"] == old["routine_id"]
    assert new["created_at"] == old["created_at"]


def test_patch_deactivate_switches_sort_key_prefix(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "title": "新標題"},
            resource="/routines/{routine_id}",
        ),
        None,
    )

    assert _body(resp)["title"] == "新標題"


def test_delete_routine_endpoint(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "DELETE",
            routine_id="rtn_001",
            query={"client_request_id": "req-del-1"},
            resource="/routines/{routine_id}",
        ),
        None,
    )

    assert resp["statusCode"] == 200
    assert _body(resp)["active"] is False
    assert store["versions"][1]["current_sort_key"].startswith("I#")



def test_patch_replays_same_request(store):
    _seed_routine(store)
    patch = _event(
        "PATCH",
        routine_id="rtn_001",
        body={"client_request_id": "req-2", "title": "改吃胃藥"},
        resource="/routines/{routine_id}",
    )
    first = _body(handler.handler(patch, None))
    second = handler.handler(patch, None)

    assert second["statusCode"] == 200
    assert _body(second) == first
    assert len(store["versions"]) == 2  # 沒有多產生版本


def test_patch_conflicts_on_same_request_id_different_payload(store):
    _seed_routine(store)
    handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "title": "改吃胃藥"},
            resource="/routines/{routine_id}",
        ),
        None,
    )
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "title": "改吃感冒藥"},
            resource="/routines/{routine_id}",
        ),
        None,
    )
    assert _code(resp) == (409, "IDEMPOTENCY_CONFLICT")


def test_patch_requires_updatable_field(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2"},
            resource="/routines/{routine_id}",
        ),
        None,
    )
    assert _code(resp) == (400, "INVALID_PARAMETER")


def test_patch_rejects_server_owned_field(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "elder_id": "eld_999"},
            resource="/routines/{routine_id}",
        ),
        None,
    )
    assert _code(resp) == (400, "INVALID_PARAMETER")


def test_patch_unknown_routine_is_404(store):
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_404",
            body={"client_request_id": "req-2", "title": "改吃胃藥"},
            resource="/routines/{routine_id}",
        ),
        None,
    )
    assert _code(resp) == (404, "ROUTINE_NOT_FOUND")


def test_patch_rejected_for_elder_caller(store):
    _seed_routine(store)
    resp = handler.handler(
        _event(
            "PATCH",
            routine_id="rtn_001",
            body={"client_request_id": "req-2", "title": "改吃胃藥"},
            claims=_elder_claims(),
            resource="/routines/{routine_id}",
        ),
        None,
    )
    assert _code(resp) == (403, "FORBIDDEN")


# -----------------------------------------------------------------------------
# POST /routines/{routine_id}/complete
# -----------------------------------------------------------------------------

COMPLETE_RESOURCE = "/routines/{routine_id}/complete"


def _complete_event(**overrides):
    kwargs = {
        "body": {"date": "2026-07-14"},
        "routine_id": "rtn_001",
        "resource": COMPLETE_RESOURCE,
    }
    kwargs.update(overrides)
    return _event("POST", **kwargs)


def test_complete_writes_manual_event_and_returns_done(store):
    _seed_routine(store)
    resp = handler.handler(_complete_event(), None)

    assert resp["statusCode"] == 200
    assert _body(resp) == {
        "routine_id": "rtn_001",
        "title": "吃血壓藥",
        "type": "medication",
        "scheduled_at": "2026-07-14T09:00:00+08:00",
        "status": "done",
        "completed_at": domain.to_iso(NOW),
        "completed_by": "caregiver",
    }

    written = list(store["events"].values())[0]
    assert written["source"] == "manual"
    assert written["extraction_track"] == "manual"
    assert written["type"] == "medication"  # 沿用完成當下的 routine type
    assert written["routine_date"] == "2026-07-14"
    assert written["routine_version"] == 1


def test_complete_defaults_to_today(store):
    _seed_routine(store)
    resp = handler.handler(_complete_event(body={}), None)
    assert _body(resp)["scheduled_at"] == "2026-07-14T09:00:00+08:00"


def test_complete_by_elder_records_elder(store):
    _seed_routine(store)
    resp = handler.handler(_complete_event(claims=_elder_claims()), None)
    assert _body(resp)["completed_by"] == "elder"


def test_complete_is_idempotent(store):
    _seed_routine(store)
    first = _body(handler.handler(_complete_event(), None))
    second = handler.handler(_complete_event(), None)

    assert _body(second) == first
    assert len(store["events"]) == 1  # 不重複寫入事件


def test_complete_hits_existing_conversation_completion(store):
    """先由對話完成過，手動確認命中同一 canonical event，不覆寫完成資料。"""
    _seed_routine(store)
    event_id = db.event_id_for(ELDER_ID, domain.completion_event_key("rtn_001", "2026-07-14"))
    store["events"][event_id] = {
        "event_id": event_id,
        "elder_id": ELDER_ID,
        "routine_id": "rtn_001",
        "routine_version": 1,
        "routine_date": "2026-07-14",
        "ts": "2026-07-14T09:05:00.000+08:00",
        "completed_by": "conversation",
    }

    body = _body(handler.handler(_complete_event(), None))
    assert body["completed_by"] == "conversation"
    assert body["completed_at"] == "2026-07-14T09:05:00.000+08:00"
    assert len(store["events"]) == 1


def test_complete_rejects_unscheduled_date(store):
    _seed_routine(store, schedule={"freq": "weekly", "weekday": 3, "time": "09:00"})
    # 2026-07-14 為週二，週三的 routine 當日沒有排程
    assert _code(handler.handler(_complete_event(), None)) == (400, "ROUTINE_NOT_SCHEDULED")


def test_complete_rejects_inactive_routine(store):
    _seed_routine(store, active=False)
    assert _code(handler.handler(_complete_event(), None)) == (400, "ROUTINE_NOT_SCHEDULED")


def test_complete_unknown_routine_is_404(store):
    resp = handler.handler(_complete_event(routine_id="rtn_404"), None)
    assert _code(resp) == (404, "ROUTINE_NOT_FOUND")


def test_complete_rejects_bad_date(store):
    _seed_routine(store)
    resp = handler.handler(_complete_event(body={"date": "2026/07/14"}), None)
    assert _code(resp) == (400, "INVALID_PARAMETER")


def test_internal_error_is_masked(store, monkeypatch):
    """資料庫錯誤只回穩定錯誤碼，訊息不外流。"""
    def boom(*args, **kwargs):
        raise db.DBError("routines table ProvisionedThroughputExceeded")

    monkeypatch.setattr(db, "list_current_routines", boom)
    resp = handler.handler(_event("GET", params={"elder_id": ELDER_ID}), None)

    assert _code(resp) == (500, "INTERNAL_ERROR")
    assert "ProvisionedThroughput" not in resp["body"]


def test_unsupported_method_is_400(store):
    assert _code(handler.handler(_event("DELETE", routine_id="rtn_001"), None)) == (
        400,
        "INVALID_PARAMETER",
    )
