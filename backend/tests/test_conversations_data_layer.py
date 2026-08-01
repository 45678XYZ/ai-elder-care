"""conversations 資料層測試（moto）。

鎖住統計計算對話輪數的前提（docs/framework.md 與 docs/api.md）：只算已完成的 turn、
只走 conversations-by-time（session metadata 不入索引），且日界上下界都要完整涵蓋。
"""

import importlib

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "conversations-test"
ELDER = "eld_a1b2c3d4e5f6"


@pytest.fixture
def db(monkeypatch):
    """在 moto 環境下重新載入 db 模組，讓表名與全域連線都指向假環境。"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", TABLE_NAME)

    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "record_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "record_id", "AttributeType": "S"},
                {"AttributeName": "conversation_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "conversations-by-time",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "conversation_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        module = importlib.reload(importlib.import_module("src.shared.db"))
        yield module

    # 還原全域連線，避免污染其他測試
    importlib.reload(importlib.import_module("src.shared.db"))


def put_turn(db, created_at: str, conversation_id: str, request_status: str = "completed"):
    table = db.get_dynamodb_resource().Table(TABLE_NAME)
    table.put_item(
        Item={
            "elder_id": ELDER,
            "record_id": f"TURN#{conversation_id}",
            "item_type": "conversation",
            "conversation_id": conversation_id,
            "conversation_time_key": f"{created_at}#{conversation_id}",
            "created_at": created_at,
            "request_status": request_status,
            "session_id": "ses_1",
            "elder_transcript": "我吃過血壓藥了",
        }
    )


def put_session(db, session_id: str = "ses_1"):
    table = db.get_dynamodb_resource().Table(TABLE_NAME)
    table.put_item(
        Item={
            "elder_id": ELDER,
            "record_id": f"SESSION#{session_id}",
            "item_type": "session",
            "session_id": session_id,
            "state": "closed",
            "turn_count": 2,
        }
    )


def test_list_turn_times_returns_completed_turns_in_order(db):
    put_turn(db, "2026-07-13T20:10:00.000+08:00", "cnv_2")
    put_turn(db, "2026-07-13T09:05:00.000+08:00", "cnv_1")

    assert db.list_turn_times(ELDER, "2026-07-13", "2026-07-13") == [
        "2026-07-13T09:05:00.000+08:00",
        "2026-07-13T20:10:00.000+08:00",
    ]


def test_saved_conversation_is_countable_as_an_interaction(db):
    """寫入端與統計讀取端的接縫。

    寫出來的 turn 必須被 `list_turn_times` 讀到——少了
    `request_status` 就會被過濾條件整批濾掉，統計無聲變 0。
    """
    put_turn(db, "2026-07-13T09:05:00.000+08:00", "cnv_saved")

    assert db.list_turn_times(ELDER, "2026-07-13", "2026-07-13") == [
        "2026-07-13T09:05:00.000+08:00"
    ]



def test_list_turn_times_skips_unfinished_and_failed_turns(db):
    put_turn(db, "2026-07-13T09:05:00.000+08:00", "cnv_1")
    put_turn(db, "2026-07-13T10:00:00.000+08:00", "cnv_2", request_status="failed")
    put_turn(db, "2026-07-13T11:00:00.000+08:00", "cnv_3", request_status="processing")

    assert db.list_turn_times(ELDER, "2026-07-13", "2026-07-13") == [
        "2026-07-13T09:05:00.000+08:00"
    ]


def test_list_turn_times_ignores_session_metadata(db):
    put_session(db)
    put_turn(db, "2026-07-13T09:05:00.000+08:00", "cnv_1")

    assert db.list_turn_times(ELDER, "2026-07-13", "2026-07-13") == [
        "2026-07-13T09:05:00.000+08:00"
    ]


def test_list_turn_times_covers_both_day_boundaries(db):
    put_turn(db, "2026-07-11T23:59:59.999+08:00", "cnv_before")
    put_turn(db, "2026-07-12T00:00:00.000+08:00", "cnv_first")
    put_turn(db, "2026-07-13T23:59:59.999+08:00", "cnv_last")
    put_turn(db, "2026-07-14T00:00:00.000+08:00", "cnv_after")

    assert db.list_turn_times(ELDER, "2026-07-12", "2026-07-13") == [
        "2026-07-12T00:00:00.000+08:00",
        "2026-07-13T23:59:59.999+08:00",
    ]


def test_list_turn_times_scopes_to_the_elder(db):
    put_turn(db, "2026-07-13T09:05:00.000+08:00", "cnv_1")

    assert db.list_turn_times("eld_ffffffffffff", "2026-07-13", "2026-07-13") == []
