"""handle_get_recent_conversations 工具函數的單元測試。

當輪 context 的來源是 session 的 `recent_conversation_ids` 加 Base table 強一致讀取
（docs/framework.md 的 Turn 欄位段落），因此驗證：
- 只回目前 active session 內、已完成的對話，且為舊→新的正序
- 仍在飛的當前輪與失敗的 turn 不得餵給模型
- 沒有 active session、空資料、缺 elder_id、limit 邊界的處置
"""

import importlib

import boto3
import pytest
from moto import mock_aws

CONVERSATIONS_TABLE = "conversations-test"
ELDER = "eld_test_conv_001"
SESSION = "ses_01J8"


@pytest.fixture
def tools_handler(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)

    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName=CONVERSATIONS_TABLE,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "record_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "record_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        importlib.reload(importlib.import_module("src.shared.db"))
        importlib.reload(importlib.import_module("src.shared.sessions"))
        tools = importlib.reload(importlib.import_module("src.handlers.tools"))
        yield tools

    for name in ("src.shared.db", "src.shared.sessions", "src.handlers.tools"):
        importlib.reload(importlib.import_module(name))


def table():
    return boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE)


def seed_session(conversation_ids, *, state="active", session_id=SESSION):
    table().put_item(
        Item={
            "elder_id": ELDER,
            "record_id": f"SESSION#{session_id}",
            "item_type": "session",
            "session_id": session_id,
            "state": state,
            "started_at": "2026-07-30T10:00:00.000+08:00",
            "last_activity_at": "2026-07-30T10:30:00.000+08:00",
            "turn_ids": list(conversation_ids),
            "turn_count": len(conversation_ids),
            "inflight_turn_ids": [],
            "inflight_turn_count": 0,
            "recent_conversation_ids": list(conversation_ids),
            "input_bytes": 0,
        }
    )


def seed_turn(index, *, request_status="completed", replied=True):
    conversation_id = f"cnv_{index:03d}"
    item = {
        "elder_id": ELDER,
        "record_id": f"TURN#{conversation_id}",
        "item_type": "conversation",
        "conversation_id": conversation_id,
        "session_id": SESSION,
        "created_at": f"2026-07-30T10:{index:02d}:00.000+08:00",
        "elder_transcript": f"長者說的第 {index} 句話",
        "request_status": request_status,
    }
    if replied:
        item["ai_respond_text"] = f"AI 的第 {index} 個回覆"
    table().put_item(Item=item)
    return conversation_id


def seed_conversations(n=3):
    """一個 active session 加上 n 輪已完成的對話。"""
    ids = [seed_turn(index) for index in range(n)]
    seed_session(ids)
    return ids


# ---------------------------------------------------------------------------
# 正常查詢測試
# ---------------------------------------------------------------------------

def test_returns_success_with_turns(tools_handler):
    """正常查詢應回傳 status=success 以及對應的 turns。"""
    seed_conversations(3)
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["status"] == "success"
    assert result["elder_id"] == ELDER
    assert result["count"] == 3
    assert len(result["turns"]) == 3


def test_turns_are_in_chronological_order(tools_handler):
    """turns 必須是舊→新排列（讓 AI 能自然閱讀對話流）。"""
    seed_conversations(3)
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    times = [t["time"] for t in result["turns"]]
    assert times == sorted(times), "turns 必須是時間正序（舊→新）"


def test_turns_contain_only_expected_fields(tools_handler):
    """回傳的每個 turn 只應包含 time / elder / ai，不夾帶其他 metadata。"""
    seed_conversations(1)
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    turn = result["turns"][0]
    allowed_keys = {"time", "elder", "ai"}
    assert set(turn.keys()) <= allowed_keys, f"turn 含有非預期欄位: {set(turn.keys()) - allowed_keys}"


def test_time_field_is_truncated_to_minute(tools_handler):
    """time 欄位應只保留到 YYYY-MM-DDTHH:MM（16 字元），不含秒與時區。"""
    seed_conversations(1)
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    t = result["turns"][0]["time"]
    assert len(t) == 16, f"time 欄位長度應為 16，實際為 {len(t)}: {t}"


def test_only_the_latest_turns_are_returned(tools_handler):
    """回顧的是「最近」幾句，不是任意幾句。"""
    seed_conversations(5)
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER, "limit": "2"})
    assert [t["elder"] for t in result["turns"]] == [
        "長者說的第 3 句話",
        "長者說的第 4 句話",
    ]


# ---------------------------------------------------------------------------
# 只回已完成的當輪 context
# ---------------------------------------------------------------------------

def test_in_flight_turn_is_not_returned(tools_handler):
    """當前這一輪還沒有 AI 回覆，餵給模型只會讓它看到半截對話。"""
    ids = [seed_turn(0), seed_turn(1, request_status="processing", replied=False)]
    seed_session(ids)

    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["count"] == 1
    assert result["turns"][0]["elder"] == "長者說的第 0 句話"


def test_failed_turn_is_not_returned(tools_handler):
    ids = [seed_turn(0), seed_turn(1, request_status="failed", replied=False)]
    seed_session(ids)

    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["count"] == 1


def test_turns_of_a_closed_session_are_not_returned(tools_handler):
    """session 已收斂代表那段對話結束了，不屬於當輪 context。"""
    ids = [seed_turn(index) for index in range(2)]
    seed_session(ids, state="closed")

    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["status"] == "success"
    assert result["turns"] == []


# ---------------------------------------------------------------------------
# 邊界情況測試
# ---------------------------------------------------------------------------

def test_empty_conversations_returns_empty_turns(tools_handler):
    """沒有任何對話紀錄時，應回傳空的 turns 而非 error。"""
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["turns"] == []


def test_new_session_without_turns_returns_empty(tools_handler):
    """session 的第一輪還在飛，沒有更早的脈絡可回顧。"""
    seed_session([])
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["count"] == 0


def test_missing_elder_id_returns_error(tools_handler):
    """缺少 elder_id 時應回傳 status=error。"""
    result = tools_handler.handle_get_recent_conversations({})
    assert result["status"] == "error"
    assert "elder_id" in result["message"]


def test_limit_is_capped_at_15(tools_handler):
    """limit 超過 15 時應自動截斷為 15，不報錯。"""
    seed_conversations(5)
    result = tools_handler.handle_get_recent_conversations(
        {"elder_id": ELDER, "limit": "999"}
    )
    assert result["status"] == "success"
    assert result["count"] <= 5


def test_limit_param_respected(tools_handler):
    """limit 參數有效時應限制回傳筆數。"""
    seed_conversations(5)
    result = tools_handler.handle_get_recent_conversations(
        {"elder_id": ELDER, "limit": "2"}
    )
    assert result["status"] == "success"
    assert result["count"] == 2
