"""src.handlers.tools 單元測試：Action Group 6 大工具 handler 邏輯驗證。"""

import json

import pytest

from src.handlers import tools
from src.shared import db


def test_handle_get_today_routines_success(monkeypatch):
    """測試 get_today_routines 工具處理函式。"""
    monkeypatch.setattr(
        db,
        "get_daily_routines",
        lambda eid, d_str: {
            "date": d_str,
            "items": [
                {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "pending"}
            ]
        }
    )

    res = tools.handle_get_today_routines({"elder_id": "eld_001", "date": "2026-07-28"})
    assert res["status"] == "success"
    assert res["data"]["date"] == "2026-07-28"
    assert len(res["data"]["items"]) == 1
    assert res["data"]["items"][0]["routine_id"] == "rtn_001"


def test_handle_complete_routine_success(monkeypatch):
    """測試 complete_routine 工具處理函式。"""
    monkeypatch.setattr(
        db,
        "complete_routine_with_event",
        lambda **kwargs: {
            "routine_id": kwargs["routine_id"],
            "status": "done",
            "completed_at": kwargs["ts"],
            "completed_by": kwargs["source"]
        }
    )

    res = tools.handle_complete_routine({
        "elder_id": "eld_001",
        "routine_id": "rtn_001",
        "date": "2026-07-28"
    })
    assert res["status"] == "success"
    assert res["data"]["routine_id"] == "rtn_001"
    assert res["data"]["status"] == "done"


def test_handle_create_routine_success(monkeypatch):
    """測試 create_routine 工具處理函式。"""
    monkeypatch.setattr(db, "create_routine", lambda item: item)

    res = tools.handle_create_routine({
        "elder_id": "eld_001",
        "title": "看心臟科",
        "type": "other",
        "time": "15:00",
        "freq": "once",
        "date": "2026-07-29"
    })
    assert res["status"] == "success"
    assert res["data"]["title"] == "看心臟科"
    assert res["data"]["schedule"]["freq"] == "once"
    assert res["data"]["schedule"]["date"] == "2026-07-29"


def test_handle_get_recent_events_success(monkeypatch):
    """測試 get_recent_events 工具處理函式。"""
    monkeypatch.setattr(
        db,
        "list_events",
        lambda **kwargs: ([{"event_id": "evt_001", "type": "wellbeing"}], None)
    )

    res = tools.handle_get_recent_events({"elder_id": "eld_001", "event_type": "wellbeing"})
    assert res["status"] == "success"
    assert res["count"] == 1
    assert res["data"][0]["type"] == "wellbeing"


def test_handle_get_elder_profile_success(monkeypatch):
    """測試 get_elder_profile 工具處理函式。"""
    monkeypatch.setattr(
        db,
        "get_elder",
        lambda eid: {
            "elder_id": eid,
            "name": "林阿蘭",
            "nickname": "阿蘭嬤",
            "health_notes": ["高血壓歷史"],
            "family": [{"name": "小明", "relation": "兒子"}],
            "preferences": {"tea": "高山烏龍茶"}
        }
    )

    res = tools.handle_get_elder_profile({"elder_id": "eld_001"})
    assert res["status"] == "success"
    assert res["data"]["name"] == "林阿蘭"
    assert res["data"]["preferences"]["tea"] == "高山烏龍茶"


def test_handle_remind_pending_routines_success(monkeypatch):
    """測試 remind_pending_routines 工具處理函式。"""
    monkeypatch.setattr(
        db,
        "get_daily_routines",
        lambda eid, d_str: {
            "date": d_str,
            "items": [
                {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "pending"},
                {"routine_id": "rtn_002", "title": "量血壓", "status": "done"}
            ]
        }
    )

    res = tools.handle_remind_pending_routines({"elder_id": "eld_001", "date": "2026-07-28"})
    assert res["status"] == "success"
    assert res["pending_count"] == 1
    assert res["pending_routines"][0]["routine_id"] == "rtn_001"


def test_tools_lambda_handler_flow(monkeypatch):
    """測試完整 Bedrock Action Group Lambda handler 轉發流程。"""
    monkeypatch.setattr(
        db,
        "get_daily_routines",
        lambda eid, d_str: {"date": d_str, "items": []}
    )

    event = {
        "messageVersion": "1.0",
        "actionGroup": "ElderCareRoutinesTools",
        "function": "get_today_routines",
        "sessionId": "eld_001",
        "parameters": [
            {"name": "date", "type": "string", "value": "2026-07-28"}
        ]
    }

    resp = tools.handler(event, None)
    assert resp["messageVersion"] == "1.0"
    assert resp["response"]["actionGroup"] == "ElderCareRoutinesTools"
    assert resp["response"]["function"] == "get_today_routines"

    body_text = resp["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    body_json = json.loads(body_text)
    assert body_json["status"] == "success"
