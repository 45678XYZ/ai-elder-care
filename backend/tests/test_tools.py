"""src.handlers.tools 單元測試：Action Group 7 大工具 handler 邏輯驗證。

涵蓋項目：
- 工具一至六：正常成功路徑與缺少必填參數的錯誤路徑
- 工具七 (notify_caregiver)：醫療級安全機制四情境
  - emergency 初次警報寫入 DB 與狀態鎖
  - critical_escalation 強制繞過冷卻
  - mitigation 設為「⚠️ 待家屬確認」（不轉綠燈）
  - 無未結案警報時忽略 mitigation（防止誤報平安）
- Lambda handler 完整 Bedrock 傳入格式轉發流程
"""

import json
import time

import pytest

from src.handlers import tools
from src.shared import db


# =============================================================================
# 工具一：get_today_routines
# =============================================================================

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


def test_handle_get_today_routines_missing_elder_id():
    """測試缺少 elder_id 時回傳 error。"""
    res = tools.handle_get_today_routines({"date": "2026-07-28"})
    assert res["status"] == "error"
    assert "elder_id" in res["message"]


# =============================================================================
# 工具二：complete_routine
# =============================================================================

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


def test_handle_complete_routine_missing_params():
    """測試缺少必填參數時回傳 error。"""
    res = tools.handle_complete_routine({"elder_id": "eld_001"})
    assert res["status"] == "error"


# =============================================================================
# 工具三：create_routine
# =============================================================================

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


# =============================================================================
# 工具四：get_recent_events
# =============================================================================

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


# =============================================================================
# 工具五：get_elder_profile
# =============================================================================

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


# =============================================================================
# 工具六：remind_pending_routines
# =============================================================================

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


# =============================================================================
# 工具七：notify_caregiver — 醫療級安全機制四大情境測試
# =============================================================================

def _reset_emergency_state():
    """清除模組層級的 _emergency_state 狀態鎖（測試隔離用）。"""
    tools._emergency_state.clear()


def _mock_create_event(monkeypatch):
    """共用的 db.create_event mock helper。"""
    created_events = []
    monkeypatch.setattr(db, "create_event", lambda data: created_events.append(data) or data)
    return created_events


# 情境一：emergency 初次警報 — 正常發送並建立 In-Memory 狀態鎖與 DB 事件
def test_notify_emergency_first_alert_success(monkeypatch):
    """emergency 初次警報：應成功發送並在 _emergency_state 鎖定 event_id。"""
    _reset_emergency_state()
    created_events = _mock_create_event(monkeypatch)

    res = tools.handle_notify_caregiver({
        "elder_id": "eld_001",
        "category": "emergency",
        "message": "長者反映在浴室跌倒，腳部劇痛無法站立"
    })

    assert res["status"] == "success"
    assert res["category"] == "emergency"
    assert "message_id" in res

    # 確認狀態鎖已建立
    state = tools._emergency_state.get("eld_001", {})
    assert state["status"] == "urgent"
    assert state["event_id"].startswith("evt_")

    # 確認已寫入 DB events
    assert len(created_events) == 1
    assert "🚨" in created_events[0]["detail"]
    assert created_events[0]["type"] == "wellbeing"


# 情境二：emergency 冷卻期內重複呼叫 — 應被攔截 (throttled)
def test_notify_emergency_cooldown_throttle(monkeypatch):
    """5 分鐘冷卻期內重複呼叫 emergency：應回傳 throttled 並不重複發信。"""
    _reset_emergency_state()
    _mock_create_event(monkeypatch)

    # 第一次：正常發送
    tools.handle_notify_caregiver({
        "elder_id": "eld_002",
        "category": "emergency",
        "message": "長者跌倒"
    })

    # 強制設定 notify_ts 在 5 分鐘內（模擬冷卻期）
    tools._emergency_state["eld_002"]["notify_ts"] = time.time() - 60  # 僅過 60 秒

    # 第二次：應被攔截
    res2 = tools.handle_notify_caregiver({
        "elder_id": "eld_002",
        "category": "emergency",
        "message": "長者繼續說腳好痛"
    })

    assert res2["status"] == "throttled"
    assert "冷卻期" in res2["message"]
    assert "active_event_id" in res2


# 情境三：critical_escalation 狀況惡化 — 強制繞過冷卻期發信
def test_notify_critical_escalation_bypasses_cooldown(monkeypatch):
    """critical_escalation：即使在冷卻期內，也應強制繞過並立即發信。"""
    _reset_emergency_state()
    _mock_create_event(monkeypatch)

    # 先建立一個處於冷卻期的緊急警報狀態
    tools._emergency_state["eld_003"] = {
        "event_id": "evt_original001",
        "notify_ts": time.time() - 60,  # 僅過 60 秒（仍在冷卻期）
        "status": "urgent"
    }

    # critical_escalation 應直接成功，不被冷卻攔截
    res = tools.handle_notify_caregiver({
        "elder_id": "eld_003",
        "category": "critical_escalation",
        "context_event_id": "evt_original001",
        "message": "長者出現大量出血，無法移動"
    })

    assert res["status"] == "success"
    assert res["category"] == "critical_escalation"


# 情境四：mitigation 長者自述緩解 — 狀態改為 unverified_mitigation（不轉綠燈）
def test_notify_mitigation_sets_unverified_status(monkeypatch):
    """mitigation：長者自述沒事時，狀態應改為 unverified_mitigation，不直接解除警報。"""
    _reset_emergency_state()
    _mock_create_event(monkeypatch)

    # 先建立一個緊急警報狀態
    tools._emergency_state["eld_004"] = {
        "event_id": "evt_fall001",
        "notify_ts": time.time() - 60,
        "status": "urgent"
    }

    res = tools.handle_notify_caregiver({
        "elder_id": "eld_004",
        "category": "mitigation",
        "context_event_id": "evt_fall001",
        "message": "長者說休息一下腳好多了"
    })

    assert res["status"] == "success"
    assert res["category"] == "mitigation"

    # 確認狀態已更新為 unverified（仍需家屬確認，非綠燈）
    state = tools._emergency_state.get("eld_004", {})
    assert state["status"] == "unverified_mitigation", (
        "⚠️ 長者自述沒事不應直接解除警報！狀態應為 unverified_mitigation，等待家屬確認。"
    )


# 情境五：mitigation 無未結案警報 — 應被忽略（不發誤報平安信）
def test_notify_mitigation_without_active_emergency_ignored(monkeypatch):
    """mitigation 在無未結案警報情況下：應回傳 ignored，不發送錯誤的平安信。"""
    _reset_emergency_state()  # 確保無任何緊急狀態

    res = tools.handle_notify_caregiver({
        "elder_id": "eld_005",
        "category": "mitigation",
        "message": "長者說沒事了"
    })

    assert res["status"] == "ignored"
    assert "無未結案緊急警報" in res["message"]


# =============================================================================
# 工具八：get_daily_summaries
# =============================================================================

def test_handle_get_daily_summaries_success(monkeypatch):
    """測試 get_daily_summaries 工具：正常回傳近期摘要清單。"""
    monkeypatch.setattr(
        db,
        "get_daily_summaries",
        lambda eid, from_d, to_d: [
            {
                "date": "2026-07-29",
                "overview": "今日身體狀況良好，按時服藥",
                "routines": {"completed": 2, "missed": 0},
                "data_status": "complete",
                "sections": {"diet": "三餐正常", "medication": "血壓藥已服用"}
            }
        ]
    )
    res = tools.handle_get_daily_summaries({"elder_id": "eld_001", "days": "1"})
    assert res["status"] == "success"
    assert res["count"] == 1
    assert res["summaries"][0]["overview"] == "今日身體狀況良好，按時服藥"


def test_handle_get_daily_summaries_missing_elder_id():
    """測試缺少 elder_id 時回傳 error。"""
    res = tools.handle_get_daily_summaries({})
    assert res["status"] == "error"


def test_handle_get_daily_summaries_days_capped(monkeypatch):
    """測試 days > 7 時自動截斷至 7 天。"""
    captured = {}
    def mock_get_summaries(eid, from_d, to_d):
        captured["from"] = from_d
        captured["to"] = to_d
        return []

    monkeypatch.setattr(db, "get_daily_summaries", mock_get_summaries)
    res = tools.handle_get_daily_summaries({"elder_id": "eld_001", "days": "99"})
    assert res["status"] == "success"
    # 7 天範圍：to - from 應為 6 天差距
    from datetime import datetime
    delta = (datetime.fromisoformat(captured["to"]) - datetime.fromisoformat(captured["from"])).days
    assert delta == 6, f"預期 6 天差距，實際得到 {delta} 天"


# =============================================================================
# Lambda handler 完整 Bedrock 傳入格式轉發流程
# =============================================================================

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


def test_tools_lambda_handler_unknown_function():
    """測試傳入未知工具名稱時，回傳 error 而非崩潰。"""
    event = {
        "messageVersion": "1.0",
        "actionGroup": "ElderCareRoutinesTools",
        "function": "nonexistent_function",
        "parameters": []
    }

    resp = tools.handler(event, None)
    body_text = resp["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
    body_json = json.loads(body_text)
    assert body_json["status"] == "error"
    assert "nonexistent_function" in body_json["message"]
