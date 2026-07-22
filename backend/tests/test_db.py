"""backend/src/shared/db.py 單元測試。"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from src.shared import db


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

    # Test create_elder
    data = {"elder_id": "eld_002", "name": "王大同"}
    created = db.create_elder(data)
    assert created["name"] == "王大同"
    mock_table.put_item.assert_called_once_with(Item=data)


@patch("src.shared.db.get_dynamodb_resource")
def test_get_daily_routines_status_calculation(mock_get_resource):
    """測試例行公事當日行程視圖與動態 status 判定 (done, pending, missed)。"""
    mock_table = MagicMock()
    mock_get_resource.return_value.Table.return_value = mock_table

    # 模擬 routines 表資料 (包含 1 個 daily, 1 個 weekly matching, 1 個 weekly non-matching)
    mock_routines = [
        {
            "routine_id": "rtn_001",
            "elder_id": "eld_001",
            "title": "早起吃血壓藥",
            "schedule": {"freq": "daily", "time": "09:00"},
            "active": True,
        },
        {
            "routine_id": "rtn_002",
            "elder_id": "eld_001",
            "title": "晚間量血壓",
            "schedule": {"freq": "daily", "time": "19:00"},
            "active": True,
        },
    ]

    # 模擬 events 表資料 (只有 rtn_001 已於 09:05 完成)
    mock_events = [
        {
            "event_id": "evt_001",
            "elder_id": "eld_001",
            "ts": "2026-07-14T09:05:00+08:00",
            "routine_id": "rtn_001",
            "source": "conversation",
        }
    ]

    def scan_side_effect(**kwargs):
        return {"Items": mock_routines}

    def query_side_effect(**kwargs):
        return {"Items": mock_events}

    mock_table.scan.side_effect = scan_side_effect
    mock_table.query.side_effect = query_side_effect

    # 假定目前時間為 2026-07-14T12:00:00+08:00 (超過 09:00 + 2h 寬限期 -> 09:00 服藥若無完成應為 missed，但 rtn_001 已完成 -> done)
    # 19:00 量血壓尚未到期 -> pending
    result = db.get_daily_routines(
        elder_id="eld_001",
        date_str="2026-07-14",
        current_iso_ts="2026-07-14T12:00:00+08:00",
        grace_period_hours=2.0,
    )

    assert result["date"] == "2026-07-14"
    items = result["items"]
    assert len(items) == 2

    # rtn_001 應為 done
    item1 = next(i for i in items if i["routine_id"] == "rtn_001")
    assert item1["status"] == "done"
    assert item1["completed_by"] == "conversation"

    # rtn_002 應為 pending (19:00 > 12:00)
    item2 = next(i for i in items if i["routine_id"] == "rtn_002")
    assert item2["status"] == "pending"


@patch("src.shared.db.get_dynamodb_client")
def test_complete_routine_with_event_transact(mock_get_client):
    """測試 transact_write_items 連動交易寫入。"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    res = db.complete_routine_with_event(
        elder_id="eld_001",
        routine_id="rtn_001",
        date_str="2026-07-14",
        event_id="evt_100",
        ts="2026-07-14T10:00:00+08:00",
        source="manual",
        detail="手動確認完成服藥",
        event_type="medication",
    )

    assert res["routine_id"] == "rtn_001"
    assert res["status"] == "done"
    assert res["completed_by"] == "manual"
    mock_client.transact_write_items.assert_called_once()
