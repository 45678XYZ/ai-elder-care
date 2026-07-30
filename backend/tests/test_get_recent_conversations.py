"""handle_get_recent_conversations 工具函數的單元測試。

驗證：
- 正常查詢回傳正確的對話格式
- 空資料回傳空 turns 陣列
- 缺少 elder_id 回傳 error
- limit 超過上限 15 時自動截斷
- 回傳的 turns 是舊→新的正序排列
"""

import importlib

import boto3
import pytest
from moto import mock_aws

CONVERSATIONS_TABLE = "conversations-test"
ELDER = "eld_test_conv_001"


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
        tools = importlib.reload(importlib.import_module("src.handlers.tools"))
        yield tools

    importlib.reload(importlib.import_module("src.shared.db"))
    importlib.reload(importlib.import_module("src.handlers.tools"))


def seed_conversations(n=3):
    """在 mocked DynamoDB 裡塞入 n 筆假對話紀錄。"""
    table = boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE)
    for i in range(n):
        table.put_item(Item={
            "elder_id": ELDER,
            "record_id": f"TURN#cnv_{i:03d}",
            "conversation_id": f"cnv_{i:03d}",
            "created_at": f"2026-07-30T10:{i:02d}:00.000+08:00",
            "elder_transcript": f"長者說的第 {i} 句話",
            "agent_reply": f"AI 的第 {i} 個回覆",
        })


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


# ---------------------------------------------------------------------------
# 邊界情況測試
# ---------------------------------------------------------------------------

def test_empty_conversations_returns_empty_turns(tools_handler):
    """沒有任何對話紀錄時，應回傳空的 turns 而非 error。"""
    result = tools_handler.handle_get_recent_conversations({"elder_id": ELDER})
    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["turns"] == []


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
