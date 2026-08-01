"""例行公事 API。規格見 docs/api.md。

- GET  /routines?elder_id=                 定義列表（App 據此排本地通知）
- GET  /routines?elder_id=&date=           當日行程視圖（missed 查詢時動態判定）
- POST /routines                           建立（201 回完整物件）
- PATCH /routines/{routine_id}             部分更新／停用
- POST /routines/{routine_id}/complete     手動確認完成（兩端皆可；寫入 manual event；
                                           無排程回 400 ROUTINE_NOT_SCHEDULED；已完成冪等）

資料流重點：

- 版本不可變：POST 建 `version=1`，PATCH 以單一 transaction 關閉舊 current 版並寫下一版；
  兩者都回強一致的最新結果，不讀最終一致的 current GSI
- 冪等 scope 由 `client_request_id` 形成：同 scope 同 payload 回既有結果，不同 payload 回
  409 IDEMPOTENCY_CONFLICT
- occurrence 狀態不落地，由 canonical completion event 與寬限期即時推導（見
  src/shared/routines.py）
"""
import json
import logging

from pydantic import ValidationError

from src.shared import auth, db, responses, routines
from src.shared.models import (
    RoutineComplete,
    RoutineCreate,
    RoutineDefinition,
    RoutineOccurrence,
    RoutineUpdate,
)
from src.shared.validation import (
    RequestValidationError,
    bad_request,
    validate,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    routine_id = (event.get("pathParameters") or {}).get("routine_id")
    is_complete = (event.get("resource") or "").endswith("/complete")

    try:
        if method == "GET" and not routine_id:
            return _list_routines(event)
        if method == "POST" and routine_id and is_complete:
            return _complete_routine(event, routine_id)
        if method == "POST" and not routine_id:
            return _create_routine(event)
        if method == "PATCH" and routine_id:
            return _update_routine(event, routine_id)
        if method == "DELETE" and routine_id:
            return _delete_routine(event, routine_id)
        return responses.error(400, "INVALID_PARAMETER", "不支援的方法或路徑")

    except (auth.AuthError, RequestValidationError) as e:
        return e.response

    except Exception:
        # 內部錯誤訊息可能含資料內容，只留在 CloudWatch，對外一律回穩定錯誤碼
        logger.exception("例行公事 API 處理失敗")
        return responses.error(500, "INTERNAL_ERROR", "伺服器內部錯誤")


# -----------------------------------------------------------------------------
# GET /routines：定義列表與當日行程
# -----------------------------------------------------------------------------

def _list_routines(event):
    params = event.get("queryStringParameters") or {}
    elder_id = params.get("elder_id")
    if not elder_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 elder_id")
    auth.assert_can_access_elder(event, elder_id)

    date_str = params.get("date")
    if date_str is None:
        return _definition_list(elder_id, params)
    if not routines.is_valid_date(date_str):
        return responses.error(400, "INVALID_PARAMETER", "date 必須為 YYYY-MM-DD")
    return _daily_view(elder_id, date_str)


def _definition_list(elder_id: str, params: dict):
    items, next_token = db.list_current_routines(
        elder_id,
        active_only=True,
        limit=_parse_limit(params),
        next_token=params.get("next_token"),
    )
    body = {"items": [_definition(item) for item in items]}
    if next_token:
        body["next_token"] = next_token
    return responses.json_response(200, body)


def _daily_view(elder_id: str, date_str: str):
    current = routines.now()
    cutoff = routines.occurrence_cutoff(date_str, current)
    versions = db.list_routine_versions_by_elder(
        elder_id, routines.versions_upper_bound(cutoff)
    )
    completions = _completion_events(
        elder_id, {version["routine_id"] for version in versions}, date_str
    )
    items = routines.resolve_occurrences(versions, completions, date_str, current)
    return responses.json_response(
        200, {"date": date_str, "items": [_occurrence(item) for item in items]}
    )


def _completion_events(elder_id: str, routine_ids: set[str], date_str: str) -> dict:
    """取該日各 routine 的 canonical completion event，回 {routine_id: event}。

    event_id 由 canonical key 穩定產生，因此可直接批次取回，不必掃時間區間——完成時間
    落在隔日的補登也能命中同一 occurrence。
    """
    by_event_id = {
        db.event_id_for(elder_id, routines.completion_event_key(routine_id, date_str)): routine_id
        for routine_id in routine_ids
    }
    if not by_event_id:
        return {}
    found = db.get_events(elder_id, list(by_event_id))
    return {by_event_id[event_id]: item for event_id, item in found.items()}


# -----------------------------------------------------------------------------
# POST /routines：建立
# -----------------------------------------------------------------------------

def _create_routine(event):
    caller = auth.get_caller(event)
    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者可建立例行公事")

    payload = validate(RoutineCreate, _parse_body(event))

    auth.assert_can_access_elder(event, payload.elder_id)

    # 冪等 scope：同一照護者以同一 client_request_id 重送必然得到同一個 routine_id
    scope = (payload.elder_id, caller.user_id, payload.client_request_id)
    routine_id = routines.stable_id("rtn_", *scope)
    schedule = payload.schedule.model_dump(exclude_none=True)
    request_hash = routines.request_hash(
        {
            "title": payload.title,
            "type": payload.type,
            "schedule": schedule,
            "remind": payload.remind,
        }
    )
    now = routines.now_iso()

    item = {
        "routine_id": routine_id,
        "version": 1,
        "elder_id": payload.elder_id,
        "is_current": True,
        "active": True,
        "remind": payload.remind,
        "title": payload.title,
        "type": payload.type,
        "schedule": schedule,
        "effective_from": now,
        "current_sort_key": routines.current_sort_key(True, now, routine_id),
        "version_time_key": routines.version_time_key(now, routine_id, 1),
        "created_by": "caregiver",
        "created_by_id": caller.user_id,
        "updated_by": "caregiver",
        "updated_by_id": caller.user_id,
        "change_request_id": routines.stable_id("chg_", *scope),
        "request_hash": request_hash,
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }

    try:
        created = db.put_routine_version(item)
    except db.ConditionFailedError:
        # 相同 scoped request 重送：同 payload 回既有建立結果，不同 payload 為衝突
        created = db.get_routine_version(routine_id, 1)
        if created is None:
            raise db.DBError("例行公事版本已存在但讀取失敗")
        _assert_same_request(created, request_hash)

    return responses.json_response(201, _definition(created))


# -----------------------------------------------------------------------------
# PATCH /routines/{routine_id}：修改
# -----------------------------------------------------------------------------


def _update_routine(event, routine_id: str):
    caller = auth.get_caller(event)
    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者可修改例行公事")

    payload = validate(RoutineUpdate, _parse_body(event))

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    changes.pop("client_request_id", None)
    if not changes:
        return responses.error(400, "INVALID_PARAMETER", "未提供可更新的欄位")

    versions = db.list_routine_versions(routine_id)
    if not versions:
        return responses.error(404, "ROUTINE_NOT_FOUND", "找不到指定的例行公事")
    current = versions[-1]
    auth.assert_can_access_elder(event, current["elder_id"])

    change_request_id = routines.stable_id(
        "chg_", routine_id, caller.user_id, payload.client_request_id
    )
    request_hash = routines.request_hash(changes)
    replay = _find_change(versions, change_request_id, request_hash)
    if replay:
        return responses.json_response(200, _definition(replay))

    now = routines.now_iso()
    next_version = int(current["version"]) + 1
    active = changes.get("active", current.get("active", True))
    item = dict(current)
    item.update(changes)
    item.update(
        {
            "version": next_version,
            "is_current": True,
            "effective_from": now,
            "version_time_key": routines.version_time_key(now, routine_id, next_version),
            # 排序用 created_at，routine 在列表中的位置才不會因改版而跳動
            "current_sort_key": routines.current_sort_key(
                active, current["created_at"], routine_id
            ),
            "updated_by": "caregiver",
            "updated_by_id": caller.user_id,
            "change_request_id": change_request_id,
            "request_hash": request_hash,
            "updated_at": now,
        }
    )
    item.pop("effective_to", None)  # 新的 current 版本尚未結束

    try:
        updated = db.replace_current_routine_version(current, item)
    except db.ConditionFailedError:
        # 版本已被並行請求推進：同一 scoped request 回同一結果，否則請 client 重試
        replay = _find_change(
            db.list_routine_versions(routine_id), change_request_id, request_hash
        )
        if replay is None:
            return responses.error(
                409, "REQUEST_IN_PROGRESS", "另一個修改正在進行，請稍後重試"
            )
        updated = replay

    return responses.json_response(200, _definition(updated))


# -----------------------------------------------------------------------------
# DELETE /routines/{routine_id}：刪除
# -----------------------------------------------------------------------------

def _delete_routine(event, routine_id: str):
    caller = auth.get_caller(event)
    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者可刪除例行公事")

    params = event.get("queryStringParameters") or {}
    client_request_id = params.get("client_request_id", "")
    if not client_request_id:
        return responses.error(400, "MISSING_REQUEST_ID", "DELETE 須提供 client_request_id query parameter")

    # 冪等重播：tombstone 已存在且 client_request_id 一致 → 直接回成功
    tombstone = db.get_routine_tombstone(routine_id)
    if tombstone:
        if tombstone.get("client_request_id") == client_request_id:
            return responses.json_response(200, {"deleted": True, "routine_id": routine_id})
        return responses.error(409, "IDEMPOTENCY_CONFLICT", "此 routine 已被另一個 request 刪除")

    versions = db.list_routine_versions(routine_id)
    if not versions:
        return responses.error(404, "ROUTINE_NOT_FOUND", "找不到指定的例行公事")
    current = versions[-1]
    auth.assert_can_access_elder(event, current["elder_id"])

    db.delete_routine(routine_id, deleted_by=caller.user_id, client_request_id=client_request_id)
    return responses.json_response(200, {"deleted": True, "routine_id": routine_id})


# -----------------------------------------------------------------------------
# POST /routines/{routine_id}/complete：手動確認完成
# -----------------------------------------------------------------------------


def _complete_routine(event, routine_id: str):
    payload = validate(RoutineComplete, _parse_body(event))


    versions = db.list_routine_versions(routine_id)
    if not versions:
        return responses.error(404, "ROUTINE_NOT_FOUND", "找不到指定的例行公事")
    caller = auth.assert_can_access_elder(event, versions[-1]["elder_id"])

    current = routines.now()
    date_str = payload.date or routines.today(current)
    version = routines.select_effective_version(
        versions, routines.occurrence_cutoff(date_str, current)
    )
    if version is None or not version.get("active", True):
        return responses.error(400, "ROUTINE_NOT_SCHEDULED", "指定日期沒有這筆例行公事")
    if routines.scheduled_at(date_str, version.get("schedule") or {}) is None:
        return responses.error(400, "ROUTINE_NOT_SCHEDULED", "指定日期沒有這筆例行公事")

    now = routines.to_iso(current)
    elder_id = version["elder_id"]
    completion, _ = db.put_event_if_absent(
        {
            "elder_id": elder_id,
            "canonical_event_key": routines.completion_event_key(routine_id, date_str),
            "extraction_track": "manual",
            "source": "manual",
            "ts": now,
            # 完成事件沿用完成當下的 routine type，摘要七類才對得起來（docs/api.md）
            "type": version.get("type", "other"),
            "detail": f"完成例行公事：{version.get('title', '')}",
            "routine_id": routine_id,
            "routine_version": int(version["version"]),
            "routine_date": date_str,
            "completed_by": "elder" if caller.role == auth.ROLE_ELDER else "caregiver",
            "created_at": now,
            "updated_at": now,
        }
    )

    occurrence = routines.resolve_occurrence(versions, completion, date_str, current)
    return responses.json_response(200, _occurrence(occurrence))


# -----------------------------------------------------------------------------
# 共用小工具
# -----------------------------------------------------------------------------

def _definition(item: dict) -> dict:
    """DynamoDB routine 版本 → api.md 的定義物件（版本、冪等鍵等內部欄位不外露）。"""
    return RoutineDefinition(
        routine_id=item["routine_id"],
        elder_id=item["elder_id"],
        title=item.get("title", ""),
        type=item.get("type", "other"),
        schedule=item.get("schedule") or {},
        remind=bool(item.get("remind", True)),
        active=bool(item.get("active", True)),
        created_by=item.get("created_by", "caregiver"),
        created_at=item.get("created_at", ""),
    ).model_dump()


def _occurrence(item: dict) -> dict:
    return RoutineOccurrence(**item).model_dump(exclude_none=True)


def _find_change(versions: list[dict], change_request_id: str, request_hash: str):
    """找出同一冪等 scope 已產生的版本；payload 不同即為衝突。"""
    existing = next(
        (v for v in versions if v.get("change_request_id") == change_request_id), None
    )
    if existing is None:
        return None
    _assert_same_request(existing, request_hash)
    return existing


def _assert_same_request(existing: dict, request_hash: str) -> None:
    if existing.get("request_hash") != request_hash:
        raise RequestValidationError(
            responses.error(
                409, "IDEMPOTENCY_CONFLICT", "相同的 client_request_id 已用於不同內容"
            )
        )


def _parse_body(event) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        raise bad_request("request body 不是合法的 JSON")
    if not isinstance(body, dict):
        raise bad_request("request body 必須是 JSON 物件")
    return body


def _parse_limit(params: dict) -> int:
    raw = params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise bad_request("limit 必須是整數")
    if not 1 <= limit <= MAX_LIMIT:
        raise bad_request(f"limit 必須介於 1 與 {MAX_LIMIT} 之間")
    return limit


