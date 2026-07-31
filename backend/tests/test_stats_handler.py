"""GET /stats handler 測試。

鎖住 docs/api.md 的統計契約：`interaction_count` 是 /chat 對話輪數、`daily` 逐日補齊、
`by_routine` 只列區間內排程過的 routine，且完成一律依 canonical completion event 判定
（同日改版、隔日補登都要收斂到同一筆 occurrence）。
"""

from datetime import datetime
import json
from unittest.mock import patch

import pytest

from src.handlers import stats as stats_handler
from src.shared import routines as rtn

ELDER = "eld_a1b2c3d4e5f6"

# 固定「現在」為 api.md 範例時間，讓 pending／missed 與日界推導都可預期
NOW = datetime(2026, 7, 14, 15, 22, tzinfo=rtn.TZ_TAIPEI)

# 期間內每天都排程的 routine
DAILY_ROUTINE = {
    "routine_id": "rtn_001",
    "version": 1,
    "elder_id": ELDER,
    "title": "吃血壓藥",
    "type": "medication",
    "schedule": {"freq": "daily", "time": "09:00"},
    "active": True,
    "is_current": True,
    "effective_from": "2026-07-01T08:00:00.000+08:00",
    "created_at": "2026-07-01T08:00:00.000+08:00",
}

# 只在今天排程一次，且排定時間還沒到 → pending
ONCE_ROUTINE = {
    "routine_id": "rtn_002",
    "version": 1,
    "elder_id": ELDER,
    "title": "量血壓",
    "type": "other",
    "schedule": {"freq": "once", "date": "2026-07-14", "time": "19:00"},
    "active": True,
    "is_current": True,
    "effective_from": "2026-07-01T08:00:00.000+08:00",
    "created_at": "2026-07-01T08:00:00.000+08:00",
}

# 區間中途才建立，只有 07-12 起才有 occurrence
MIDWAY_ROUTINE = {
    "routine_id": "rtn_003",
    "version": 1,
    "elder_id": ELDER,
    "title": "散步",
    "type": "activity",
    "schedule": {"freq": "daily", "time": "16:00"},
    "active": True,
    "is_current": True,
    "effective_from": "2026-07-12T08:00:00.000+08:00",
    "created_at": "2026-07-12T08:00:00.000+08:00",
}

# 區間內沒有任何排程 → 不得出現在 by_routine
OUT_OF_PERIOD_ROUTINE = {
    "routine_id": "rtn_004",
    "version": 1,
    "elder_id": ELDER,
    "title": "回診",
    "type": "other",
    "schedule": {"freq": "once", "date": "2026-08-01", "time": "10:00"},
    "active": True,
    "is_current": True,
    "effective_from": "2026-07-01T08:00:00.000+08:00",
    "created_at": "2026-07-01T08:00:00.000+08:00",
}

TURN_TIMES = [
    "2026-07-08T09:10:00.000+08:00",
    "2026-07-08T09:12:00.000+08:00",
    "2026-07-10T18:00:00.000+08:00",
    "2026-07-13T08:30:00.000+08:00",
    "2026-07-14T09:05:00.000+08:00",
    "2026-07-14T12:40:00.000+08:00",
    "2026-07-14T15:22:00.000+08:00",
]


def make_event(**params):
    return {
        "httpMethod": "GET",
        "queryStringParameters": {"elder_id": ELDER, **params},
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }


def completion_event(routine_id: str, date: str, version: int = 1) -> dict:
    """該 occurrence 的 canonical completion event；event_id 用產品程式碼自己的推導。"""
    return {
        "event_id": stats_handler.db.event_id_for(
            ELDER, rtn.completion_event_key(routine_id, date)
        ),
        "elder_id": ELDER,
        "routine_id": routine_id,
        "routine_date": date,
        "routine_version": version,
        "ts": f"{date}T09:20:00.000+08:00",
        "completed_by": "conversation",
    }


def body_of(response):
    assert response["headers"]["Content-Type"].startswith("application/json")
    return json.loads(response["body"])


def run_handler(
    params: dict | None = None,
    versions: list[dict] | None = None,
    completions: list[dict] | None = None,
    turn_times: list[str] | None = None,
):
    """以固定「現在」與假資料層跑 handler。"""
    found = {item["event_id"]: item for item in (completions or [])}
    with (
        patch.object(stats_handler.routines, "now", return_value=NOW),
        patch.object(
            stats_handler.db, "list_turn_times", return_value=list(turn_times or [])
        ) as list_turns,
        patch.object(
            stats_handler.db,
            "list_routine_versions_by_elder",
            return_value=list(versions or []),
        ),
        patch.object(stats_handler.db, "get_events", return_value=found) as get_events,
    ):
        response = stats_handler.handler(make_event(**(params or {})), None)
    return response, list_turns, get_events


@pytest.fixture(autouse=True)
def allow_access():
    with patch.object(stats_handler.auth, "assert_can_access_elder") as mock:
        yield mock


def test_returns_documented_shape():
    response, _, _ = run_handler(
        versions=[DAILY_ROUTINE, ONCE_ROUTINE, MIDWAY_ROUTINE, OUT_OF_PERIOD_ROUTINE],
        completions=[
            completion_event("rtn_001", date)
            for date in (
                "2026-07-08",
                "2026-07-09",
                "2026-07-10",
                "2026-07-11",
                "2026-07-12",
                "2026-07-13",
                "2026-07-14",
            )
        ],
        turn_times=TURN_TIMES,
    )

    assert response["statusCode"] == 200
    body = body_of(response)
    assert body["elder_id"] == ELDER
    assert body["today"] == {
        "interaction_count": 3,
        "last_interaction_at": "2026-07-14T15:22:00.000+08:00",
    }
    assert body["period"] == {"days": 7, "interaction_count": 7, "active_days": 4}
    assert body["routines"]["by_routine"] == [
        {"routine_id": "rtn_001", "title": "吃血壓藥", "completed": 7, "total": 7},
        {"routine_id": "rtn_002", "title": "量血壓", "completed": 0, "total": 1},
        {"routine_id": "rtn_003", "title": "散步", "completed": 0, "total": 3},
    ]
    assert body["daily"] == [
        {"date": "2026-07-08", "interaction_count": 2, "routines_completed": 1, "routines_total": 1},
        {"date": "2026-07-09", "interaction_count": 0, "routines_completed": 1, "routines_total": 1},
        {"date": "2026-07-10", "interaction_count": 1, "routines_completed": 1, "routines_total": 1},
        {"date": "2026-07-11", "interaction_count": 0, "routines_completed": 1, "routines_total": 1},
        {"date": "2026-07-12", "interaction_count": 0, "routines_completed": 1, "routines_total": 2},
        {"date": "2026-07-13", "interaction_count": 1, "routines_completed": 1, "routines_total": 2},
        {"date": "2026-07-14", "interaction_count": 3, "routines_completed": 1, "routines_total": 3},
    ]


def test_period_defaults_to_last_seven_taipei_days():
    _, list_turns, _ = run_handler()
    assert list_turns.call_args.kwargs == {
        "from_date": "2026-07-08",
        "to_date": "2026-07-14",
    }


def test_days_parameter_shrinks_the_period():
    response, list_turns, _ = run_handler(params={"days": "2"}, turn_times=TURN_TIMES)
    assert list_turns.call_args.kwargs == {
        "from_date": "2026-07-13",
        "to_date": "2026-07-14",
    }
    body = body_of(response)
    assert body["period"] == {"days": 2, "interaction_count": 4, "active_days": 2}
    assert [point["date"] for point in body["daily"]] == ["2026-07-13", "2026-07-14"]


def test_today_without_interaction_omits_last_interaction_at():
    response, _, _ = run_handler(turn_times=["2026-07-13T08:30:00.000+08:00"])
    assert body_of(response)["today"] == {"interaction_count": 0}


def test_no_routines_yields_empty_by_routine_and_zero_filled_daily():
    response, _, get_events = run_handler(turn_times=TURN_TIMES)
    body = body_of(response)
    assert body["routines"] == {"by_routine": []}
    assert all(point["routines_total"] == 0 for point in body["daily"])
    # 沒有任何版本就不必為 completion event 白跑一次批次讀取
    get_events.assert_not_called()


def test_completion_event_wins_over_schedule_change():
    """完成後才改版仍算完成：completion event 存在就固定 done（docs/api.md）。"""
    revised = {
        **DAILY_ROUTINE,
        "version": 2,
        "schedule": {"freq": "once", "date": "2026-08-20", "time": "09:00"},
        "effective_from": "2026-07-14T10:00:00.000+08:00",
    }
    response, _, _ = run_handler(
        params={"days": "1"},
        versions=[DAILY_ROUTINE, revised],
        completions=[completion_event("rtn_001", "2026-07-14")],
    )
    assert body_of(response)["routines"]["by_routine"] == [
        {"routine_id": "rtn_001", "title": "吃血壓藥", "completed": 1, "total": 1}
    ]


def test_completion_lookup_skips_days_before_the_routine_existed():
    _, _, get_events = run_handler(params={"days": "7"}, versions=[MIDWAY_ROUTINE])
    requested = get_events.call_args.args[1]
    expected = [
        stats_handler.db.event_id_for(ELDER, rtn.completion_event_key("rtn_003", date))
        for date in ("2026-07-12", "2026-07-13", "2026-07-14")
    ]
    assert sorted(requested) == sorted(expected)


def test_missing_elder_id():
    response = stats_handler.handler(
        {"queryStringParameters": {}, "requestContext": {"authorizer": {"claims": {"sub": "x"}}}},
        None,
    )
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


@pytest.mark.parametrize("days", ["0", "32", "abc", "-1", "7.5"])
def test_invalid_days(days):
    response, _, _ = run_handler(params={"days": days})
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


def test_forbidden_is_propagated_from_auth(allow_access):
    from src.shared import responses

    forbidden = responses.error(403, "FORBIDDEN", "無權存取此長者的資料")
    allow_access.side_effect = stats_handler.auth.AuthError(forbidden)
    response = stats_handler.handler(make_event(), None)
    assert response["statusCode"] == 403
    assert body_of(response)["error"]["code"] == "FORBIDDEN"


def test_db_failure_is_internal_error():
    from src.shared import db as db_module

    with (
        patch.object(stats_handler.routines, "now", return_value=NOW),
        patch.object(
            stats_handler.db,
            "list_turn_times",
            side_effect=db_module.DBError("查詢對話輪數失敗: boom"),
        ),
    ):
        response = stats_handler.handler(make_event(), None)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "INTERNAL_ERROR"
