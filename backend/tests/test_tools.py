"""src.handlers.tools 單元測試：12 個工具 handler 邏輯與分派驗證。

涵蓋項目：
- 工具一至六：正常成功路徑與缺少必填參數的錯誤路徑
- 工具七 (notify_caregiver)：醫療級安全機制四情境
  - emergency 初次警報寫入 DB 與狀態鎖
  - critical_escalation 強制繞過冷卻
  - mitigation 設為「⚠️ 待家屬確認」（不轉綠燈）
  - 無未結案警報時忽略 mitigation（防止誤報平安）
- Lambda handler 的 {tool, params} 分派流程（呼叫端為 AgentCore Runtime）
"""

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
            "completed_by": kwargs.get("completed_by", "conversation")
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
            "habit_note": "喜歡喝高山烏龍茶、早起散步"
        }
    )

    res = tools.handle_get_elder_profile({"elder_id": "eld_001"})
    assert res["status"] == "success"
    assert res["data"]["name"] == "林阿蘭"
    assert res["data"]["habit_note"] == "喜歡喝高山烏龍茶、早起散步"


# =============================================================================
# 工具 5.1：update_elder_profile
# =============================================================================

def _fake_elder_store(monkeypatch):
    """把 db 的長者讀寫換成一份記憶體資料，回傳那份資料供斷言。

    健康註記走 append_health_note（原子 append），與 update_elder 是兩條不同的路，
    測試得把兩條都接起來才反映真實流程。
    """
    stored = {
        "elder_id": "eld_001",
        "nickname": "阿蘭嬤",
        "health_notes": [{"note_id": "hn_old", "text": "高血壓歷史", "source": "caregiver"}],
        "habit_note": "喜歡喝高山烏龍茶",
    }

    def mock_append(eid, note):
        stored["health_notes"] = stored["health_notes"] + [
            {"note_id": "hn_new", "text": note["text"], "source": note["source"]}
        ]
        return dict(stored)

    def mock_update(eid, patch):
        stored.update(patch)
        return dict(stored)

    monkeypatch.setattr(db, "get_elder", lambda eid: dict(stored))
    monkeypatch.setattr(db, "append_health_note", mock_append)
    monkeypatch.setattr(db, "update_elder", mock_update)
    return stored


def test_handle_update_elder_profile_success(monkeypatch):
    """測試 update_elder_profile 工具處理函式。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "nickname": "超級阿蘭嬤",
        "health_note_to_add": "對阿司匹林過敏",
        "habit_note_to_append": "不喜歡吃香菜"
    })

    assert res["status"] == "success"
    assert res["data"]["nickname"] == "超級阿蘭嬤"
    # 回給模型的是攤平後的文字，不含 note_id
    assert "對阿司匹林過敏" in res["data"]["health_notes"]
    assert "高血壓歷史" in res["data"]["health_notes"]
    assert "不喜歡吃香菜" in res["data"]["habit_note"]
    assert "喜歡喝高山烏龍茶" in res["data"]["habit_note"]

    # 落地的那一筆要標成 agent，照護者才分得出這是 AI 從談話裡聽來的
    added = [n for n in stored["health_notes"] if n["text"] == "對阿司匹林過敏"]
    assert len(added) == 1
    assert added[0]["source"] == "agent"


def test_handle_update_elder_profile_health_note_uses_atomic_append(monkeypatch):
    """健康註記不得走 update_elder 整份覆寫，否則會蓋掉併發寫入的內容。"""
    _fake_elder_store(monkeypatch)

    patched_fields = []
    original_update = db.update_elder
    monkeypatch.setattr(
        db, "update_elder",
        lambda eid, patch: (patched_fields.extend(patch.keys()), original_update(eid, patch))[1]
    )

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "health_note_to_add": "膝蓋退化",
    })

    assert res["status"] == "success"
    assert "health_notes" not in patched_fields


def test_handle_update_elder_profile_skips_duplicate_health_note(monkeypatch):
    """已經有的註記不重複加；此時沒有任何欄位可更新。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "health_note_to_add": "高血壓歷史",
    })

    assert res["status"] == "error"
    assert len(stored["health_notes"]) == 1


def test_handle_update_elder_profile_lang_preference(monkeypatch):
    """測試透過工具切換語言偏好。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "lang_preference": "hak",
    })

    assert res["status"] == "success"
    assert "lang_preference" in res["updated_fields"]
    assert stored["lang_preference"] == "hak"
    assert res["data"]["lang_preference"] == "hak"


def test_handle_update_elder_profile_hakka_dialect(monkeypatch):
    """測試透過工具切換客語腔調。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "hakka_dialect": "htia_hailu",
    })

    assert res["status"] == "success"
    assert "hakka_dialect" in res["updated_fields"]
    assert stored["hakka_dialect"] == "htia_hailu"
    assert res["data"]["hakka_dialect"] == "htia_hailu"


def test_handle_update_elder_profile_invalid_lang_ignored(monkeypatch):
    """無效的語言值不報錯但不寫入。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "lang_preference": "invalid-lang",
    })

    # 沒有任何有效欄位可更新
    assert res["status"] == "error"
    assert "lang_preference" not in stored


def test_handle_update_elder_profile_invalid_dialect_ignored(monkeypatch):
    """無效的腔調值不報錯但不寫入。"""
    stored = _fake_elder_store(monkeypatch)

    res = tools.handle_update_elder_profile({
        "elder_id": "eld_001",
        "hakka_dialect": "htia_unknown",
    })

    assert res["status"] == "error"
    assert "hakka_dialect" not in stored


def test_handle_get_elder_profile_flattens_health_notes(monkeypatch):
    """給模型的檔案只帶文字，note_id 這種內部識別碼不進 prompt。"""
    _fake_elder_store(monkeypatch)

    res = tools.handle_get_elder_profile({"elder_id": "eld_001"})

    assert res["status"] == "success"
    assert res["data"]["health_notes"] == ["高血壓歷史"]


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
    """共用的 db.put_event_if_absent 與 SNS mock helper。"""
    created_events = []
    def _fake_put(data):
        item = dict(data)
        item.setdefault("event_id", f"evt_{item.get('canonical_event_key', 'test')}")
        created_events.append(item)
        return item, True

    monkeypatch.setattr(db, "put_event_if_absent", _fake_put)
    monkeypatch.setattr(tools, "_publish_to_caregivers", lambda eid, sub, body: "msg_mock_123")
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
    assert created_events[0]["type"] == "safety"



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
        "list_daily_summaries",
        lambda eid, from_d, to_d: (
            [
                {
                    "date": "2026-07-29",
                    "overview": "今日身體狀況良好，按時服藥",
                    "routines": {"completed": 2, "missed": 0},
                    "data_status": "complete",
                    "sections": {"diet": "三餐正常", "medication": "血壓藥已服用"}
                }
            ],
            None
        )
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
        return [], None

    monkeypatch.setattr(db, "list_daily_summaries", mock_get_summaries)

    res = tools.handle_get_daily_summaries({"elder_id": "eld_001", "days": "99"})
    assert res["status"] == "success"
    # 7 天範圍：to - from 應為 6 天差距
    from datetime import datetime
    delta = (datetime.fromisoformat(captured["to"]) - datetime.fromisoformat(captured["from"])).days
    assert delta == 6, f"預期 6 天差距，實際得到 {delta} 天"


# =============================================================================
# Lambda handler 完整轉發流程（呼叫端為 AgentCore Runtime）
# =============================================================================

def test_tools_lambda_handler_flow(monkeypatch):
    """測試 {tool, params} 傳入格式能分派到對應 handler 並直接回 JSON 結果。"""
    monkeypatch.setattr(
        db,
        "get_daily_routines",
        lambda eid, d_str: {"date": d_str, "items": []}
    )

    event = {
        "tool": "get_today_routines",
        "params": {"elder_id": "eld_001", "date": "2026-07-28"},
    }

    resp = tools.handler(event, None)
    assert resp["status"] == "success"
    assert resp["data"]["date"] == "2026-07-28"


def test_tools_lambda_handler_unknown_function():
    """測試傳入未知工具名稱時，回傳 error 而非崩潰。"""
    event = {"tool": "nonexistent_function", "params": {}}

    resp = tools.handler(event, None)
    assert resp["status"] == "error"
    assert "nonexistent_function" in resp["message"]


# =============================================================================
# 工具十三：get_weather_forecast
# =============================================================================

def test_handle_get_weather_forecast_missing_elder_id():
    """缺少 elder_id 回傳 error。"""
    res = tools.handle_get_weather_forecast({})
    assert res["status"] == "error"
    assert "elder_id" in res["message"]


def test_handle_get_weather_forecast_missing_api_key(monkeypatch):
    """CWA_API_KEY 未配置時回傳 error。"""
    monkeypatch.setattr(tools, "_CWA_API_KEY", "")
    res = tools.handle_get_weather_forecast({"elder_id": "eld_001"})
    assert res["status"] == "error"
    assert "CWA_API_KEY" in res["message"]


def test_handle_get_weather_forecast_success(monkeypatch):
    """正常取得天氣預報。"""
    import io
    import json as json_mod

    monkeypatch.setattr(tools, "_CWA_API_KEY", "TEST-KEY-123")

    mock_response_data = {
        "records": {
            "location": [
                {
                    "locationName": "臺北市",
                    "weatherElement": [
                        {
                            "elementName": "Wx",
                            "time": [
                                {
                                    "startTime": "2026-08-01 06:00:00",
                                    "endTime": "2026-08-01 18:00:00",
                                    "parameter": {"parameterName": "多雲短暫雨"}
                                }
                            ]
                        },
                        {
                            "elementName": "MinT",
                            "time": [
                                {"parameter": {"parameterName": "25"}}
                            ]
                        },
                        {
                            "elementName": "MaxT",
                            "time": [
                                {"parameter": {"parameterName": "33"}}
                            ]
                        },
                        {
                            "elementName": "PoP",
                            "time": [
                                {"parameter": {"parameterName": "60"}}
                            ]
                        },
                    ]
                }
            ]
        }
    }

    class MockResponse:
        def read(self):
            return json_mod.dumps(mock_response_data).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: MockResponse()
    )
    monkeypatch.setattr(
        db, "get_elder",
        lambda eid: {"elder_id": eid, "address_region": "台北市大安區"}
    )

    res = tools.handle_get_weather_forecast({"elder_id": "eld_001"})
    assert res["status"] == "success"
    assert res["location"] == "臺北市"
    assert len(res["forecast"]) == 1
    assert res["forecast"][0]["weather"] == "多雲短暫雨"
    assert res["forecast"][0]["temp_low"] == 25
    assert res["forecast"][0]["temp_high"] == 33
    assert res["forecast"][0]["rain_prob"] == 60


def test_handle_get_weather_forecast_with_explicit_location(monkeypatch):
    """傳入 location 時不查 elder profile。"""
    import json as json_mod

    monkeypatch.setattr(tools, "_CWA_API_KEY", "TEST-KEY-123")

    mock_response_data = {
        "records": {
            "location": [
                {
                    "locationName": "高雄市",
                    "weatherElement": [
                        {
                            "elementName": "Wx",
                            "time": [
                                {
                                    "startTime": "2026-08-01 06:00:00",
                                    "endTime": "2026-08-01 18:00:00",
                                    "parameter": {"parameterName": "晴時多雲"}
                                }
                            ]
                        },
                        {"elementName": "MinT", "time": [{"parameter": {"parameterName": "27"}}]},
                        {"elementName": "MaxT", "time": [{"parameter": {"parameterName": "35"}}]},
                        {"elementName": "PoP", "time": [{"parameter": {"parameterName": "10"}}]},
                    ]
                }
            ]
        }
    }

    class MockResponse:
        def read(self):
            return json_mod.dumps(mock_response_data).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: MockResponse()
    )

    res = tools.handle_get_weather_forecast({
        "elder_id": "eld_001",
        "location": "高雄市"
    })
    assert res["status"] == "success"
    assert res["location"] == "高雄市"
    assert res["forecast"][0]["weather"] == "晴時多雲"


def test_handle_get_weather_forecast_api_failure(monkeypatch):
    """氣象署 API 連線失敗時回傳 error。"""
    monkeypatch.setattr(tools, "_CWA_API_KEY", "TEST-KEY-123")
    monkeypatch.setattr(
        db, "get_elder",
        lambda eid: {"elder_id": eid, "address_region": "台北市"}
    )

    def mock_urlopen_fail(req, timeout=None):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)

    res = tools.handle_get_weather_forecast({"elder_id": "eld_001"})
    assert res["status"] == "error"
    assert "暫時無法取得" in res["message"]


def test_resolve_cwa_location():
    """測試 address_region 到氣象署地區名稱的對應。"""
    assert tools._resolve_cwa_location("台北市大安區") == "臺北市"
    assert tools._resolve_cwa_location("高雄市鳳山區") == "高雄市"
    assert tools._resolve_cwa_location("花蓮縣壽豐鄉") == "花蓮縣"
    assert tools._resolve_cwa_location(None) == "臺北市"
    assert tools._resolve_cwa_location("未知地區") == "臺北市"


# =============================================================================
# 工具十四：get_events_by_time
# =============================================================================

def test_handle_get_events_by_time_missing_elder_id():
    """缺少 elder_id 回傳 error。"""
    res = tools.handle_get_events_by_time({
        "start_date": "2026-07-25",
        "end_date": "2026-07-28"
    })
    assert res["status"] == "error"
    assert "elder_id" in res["message"]


def test_handle_get_events_by_time_missing_dates():
    """缺少日期參數回傳 error。"""
    res = tools.handle_get_events_by_time({"elder_id": "eld_001"})
    assert res["status"] == "error"
    assert "start_date" in res["message"] or "end_date" in res["message"]


def test_handle_get_events_by_time_success(monkeypatch):
    """正常依日期範圍查詢事件。"""
    monkeypatch.setattr(
        db,
        "list_events",
        lambda elder_id, from_date=None, to_date=None, event_type=None, limit=50, next_token=None: (
            [
                {"event_id": "evt_001", "type": "diet", "description": "早餐吃了稀飯"},
                {"event_id": "evt_002", "type": "activity", "description": "在公園散步30分鐘"},
            ],
            None
        )
    )

    res = tools.handle_get_events_by_time({
        "elder_id": "eld_001",
        "start_date": "2026-07-25",
        "end_date": "2026-07-28"
    })
    assert res["status"] == "success"
    assert res["count"] == 2
    assert res["period"]["start"] == "2026-07-25"
    assert res["period"]["end"] == "2026-07-28"
    assert len(res["data"]) == 2


def test_handle_get_events_by_time_with_type_filter(monkeypatch):
    """帶 event_type 過濾的查詢。"""
    captured = {}

    def mock_list(elder_id, from_date=None, to_date=None, event_type=None, limit=50, next_token=None):
        captured["event_type"] = event_type
        return [{"event_id": "evt_003", "type": "diet", "description": "午餐"}], None

    monkeypatch.setattr(db, "list_events", mock_list)

    res = tools.handle_get_events_by_time({
        "elder_id": "eld_001",
        "start_date": "2026-07-25",
        "end_date": "2026-07-28",
        "event_type": "diet"
    })
    assert res["status"] == "success"
    assert captured["event_type"] == "diet"


def test_handle_get_events_by_time_db_failure(monkeypatch):
    """DB 查詢失敗回傳 error。"""
    def mock_list_fail(**kwargs):
        raise Exception("DynamoDB timeout")

    monkeypatch.setattr(db, "list_events", mock_list_fail)

    res = tools.handle_get_events_by_time({
        "elder_id": "eld_001",
        "start_date": "2026-07-25",
        "end_date": "2026-07-28"
    })
    assert res["status"] == "error"
    assert "查詢事件失敗" in res["message"]


# =============================================================================
# Lambda handler 轉發新工具
# =============================================================================

def test_tools_lambda_handler_weather(monkeypatch):
    """測試 Lambda handler 可分派到 get_weather_forecast。"""
    monkeypatch.setattr(tools, "_CWA_API_KEY", "")  # 故意缺 key 觸發 error 路徑
    event = {
        "tool": "get_weather_forecast",
        "params": {"elder_id": "eld_001"},
    }
    resp = tools.handler(event, None)
    assert resp["status"] == "error"
    assert "CWA_API_KEY" in resp["message"]


def test_tools_lambda_handler_events_by_time(monkeypatch):
    """測試 Lambda handler 可分派到 get_events_by_time。"""
    monkeypatch.setattr(
        db, "list_events",
        lambda elder_id, from_date=None, to_date=None, event_type=None, limit=50, next_token=None: ([], None)
    )
    event = {
        "tool": "get_events_by_time",
        "params": {"elder_id": "eld_001", "start_date": "2026-07-25", "end_date": "2026-07-28"},
    }
    resp = tools.handler(event, None)
    assert resp["status"] == "success"
    assert resp["count"] == 0
