"""backend/src/shared/db.py 單元測試。"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import pytest

from src.shared import db


def _conditional_check_failed(operation: str) -> ClientError:
    """條件式寫入未通過時 DynamoDB 回的錯誤。"""
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "已存在"}},
        operation,
    )


def test_convert_decimals():
    """測試 Decimal 遞迴轉碼為 int 或 float。"""
    raw = {
        "id": "eld_001",
        "age": Decimal("75"),
        "score": Decimal("98.5"),
        "list_val": [Decimal("10"), Decimal("3.14")],
    }
    converted = db.convert_decimals(raw)
    assert converted == {
        "id": "eld_001",
        "age": 75,
        "score": 98.5,
        "list_val": [10, 3.14],
    }
    assert isinstance(converted["age"], int)
    assert isinstance(converted["score"], float)


def test_encode_decode_next_token():
    """測試 next_token 編解碼。"""
    raw_key = {"elder_id": "eld_001", "ts": "2026-07-14T09:05:00+08:00"}
    token = db.encode_next_token(raw_key)
    assert isinstance(token, str)
    decoded = db.decode_next_token(token)
    assert decoded == raw_key


@patch("src.shared.db.get_dynamodb_resource")
def test_get_and_create_elder(mock_get_resource):
    """測試 Elders 表之讀取與建立。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    # Test get_elder
    mock_table.get_item.return_value = {
        "Item": {"elder_id": "eld_001", "name": "陳阿蘭", "birth_year": Decimal("1948")}
    }
    elder = db.get_elder("eld_001")
    assert elder["elder_id"] == "eld_001"
    assert elder["birth_year"] == 1948

    # Test create_elder (explicit ID)
    data = {"elder_id": "eld_002", "name": "王大同", "address_region": "台北市大安區"}
    created = db.create_elder(data)
    assert created["name"] == "王大同"
    assert created["elder_id"] == "eld_002"
    assert created["address_region"] == "台北市大安區"
    assert "created_at" in created
    assert "updated_at" in created

    # Test create_elder (auto-generated ID with eld_ prefix)
    auto_data = {"name": "李小花"}
    auto_created = db.create_elder(auto_data)
    assert auto_created["name"] == "李小花"
    assert auto_created["elder_id"].startswith("eld_")
    assert "created_at" in auto_created
    assert "updated_at" in auto_created


@patch("src.shared.db.get_dynamodb_resource")
def test_update_elder(mock_get_resource):
    """測試 Elders 表之 PATCH 更新與 updated_at 自動刷新。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    mock_table.update_item.return_value = {
        "Attributes": {
            "elder_id": "eld_001",
            "name": "陳阿蘭",
            "nickname": "阿蘭姊",
            "updated_at": "2026-07-24T15:30:00+08:00",
        }
    }

    updated = db.update_elder("eld_001", {"nickname": "阿蘭姊"})
    assert updated["nickname"] == "阿蘭姊"
    assert "updated_at" in updated
    mock_table.update_item.assert_called_once()


@patch("src.shared.db.get_dynamodb_resource")
def test_put_routine_version_is_conditional(mock_get_resource):
    """測試 routine 版本不可變：同一 (routine_id, version) 已存在時不覆寫。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    item = {"routine_id": "rtn_001", "version": 1, "elder_id": "eld_001"}
    assert db.put_routine_version(item) == item
    assert (
        mock_table.put_item.call_args.kwargs["ConditionExpression"]
        == "attribute_not_exists(routine_id)"
    )

    mock_table.put_item.side_effect = _conditional_check_failed("PutItem")
    with pytest.raises(db.ConditionFailedError):
        db.put_routine_version(item)


@patch("src.shared.db.get_dynamodb_client")
def test_replace_current_routine_version_transaction(mock_get_client):
    """測試改版以單一 transaction 關閉舊 current 版並寫入下一版。"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    current = {"routine_id": "rtn_001", "version": 1, "is_current": True}
    next_version = {
        "routine_id": "rtn_001",
        "version": 2,
        "is_current": True,
        "effective_from": "2026-07-14T10:00:00.000+08:00",
    }

    assert db.replace_current_routine_version(current, next_version) == next_version

    items = mock_client.transact_write_items.call_args.kwargs["TransactItems"]
    assert len(items) == 2
    assert items[0]["Update"]["ConditionExpression"] == "is_current = :true"
    assert "REMOVE current_sort_key" in items[0]["Update"]["UpdateExpression"]
    assert items[1]["Put"]["ConditionExpression"] == "attribute_not_exists(routine_id)"
    assert items[1]["Put"]["Item"]["version"] == {"N": "2"}

    # 條件不成立代表有並行修改，交由呼叫端判斷是重送或衝突
    mock_client.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "conflict"}},
        "TransactWriteItems",
    )
    with pytest.raises(db.ConditionFailedError):
        db.replace_current_routine_version(current, next_version)


@patch("src.shared.db.get_dynamodb_resource")
def test_list_current_routines_uses_sparse_index(mock_get_resource):
    """測試定義列表查 sparse GSI，且只取 active 前綴。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table
    mock_table.query.return_value = {"Items": [{"routine_id": "rtn_001"}]}

    items, next_token = db.list_current_routines("eld_001")

    assert items == [{"routine_id": "rtn_001"}]
    assert next_token is None
    kwargs = mock_table.query.call_args.kwargs
    assert kwargs["IndexName"] == "routines-current-by-elder"
    assert "begins_with(current_sort_key, :prefix)" in kwargs["KeyConditionExpression"]
    assert kwargs["ExpressionAttributeValues"][":prefix"] == "A#"


@patch("src.shared.db.get_dynamodb_resource")
def test_put_event_if_absent_returns_existing(mock_get_resource):
    """測試 canonical event 冪等：已存在時回既有事件而不覆寫。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    data = {
        "elder_id": "eld_001",
        "canonical_event_key": "routine_completion#rtn_001#2026-07-14",
        "type": "medication",
        "detail": "完成例行公事：吃血壓藥",
    }

    created, is_new = db.put_event_if_absent(data)
    assert is_new is True
    assert created["event_id"] == db.event_id_for("eld_001", data["canonical_event_key"])
    assert created["event_time_key"].endswith(created["event_id"])

    mock_table.put_item.side_effect = _conditional_check_failed("PutItem")
    mock_table.get_item.return_value = {"Item": {"event_id": created["event_id"], "revision": 1}}

    existing, is_new = db.put_event_if_absent(data)
    assert is_new is False
    assert existing["event_id"] == created["event_id"]


@patch("src.shared.db.get_dynamodb_resource")
def test_save_and_get_recent_conversations(mock_get_resource):
    """測試 Conversations 表之儲存、自動帶入 cnv_ ID/時間戳記與分頁查詢。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    # 1. 測試 save_conversation 自動帶入 ID 與 created_at
    data = {
        "elder_id": "eld_001",
        "source": "system_routine_inquiry",
        "routine_id": "rtn_001",
        "ai_prompt_text": "吃藥時間到囉！",
        "elder_transcript": "我吃過了",
        "ai_respond_text": "好棒！幫你記下來了。",
    }
    saved = db.save_conversation(data)
    assert saved["elder_id"] == "eld_001"
    assert saved["conversation_id"].startswith("cnv_")
    assert "created_at" in saved
    assert saved["user_status"] == "replied"
    assert saved["system_status"] == "success"
    mock_table.put_item.assert_called_once()

    # 2. 測試 get_recent_conversations (分頁 next_token)
    mock_table.query.return_value = {
        "Items": [
            {
                "conversation_id": "cnv_001",
                "elder_id": "eld_001",
                "created_at": "2026-07-24T17:00:00+08:00",
                "elder_transcript": "今天心情好",
            }
        ],
        "LastEvaluatedKey": {"elder_id": "eld_001", "created_at": "2026-07-24T17:00:00+08:00"},
    }

    items, next_token = db.get_recent_conversations("eld_001", limit=1)
    assert len(items) == 1
    assert items[0]["conversation_id"] == "cnv_001"
    assert next_token is not None
    mock_table.query.assert_called_once()
