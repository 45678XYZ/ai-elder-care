"""daily_summaries 與當日對話查詢的資料層測試（moto）。

鎖住 docs/api.md 的覆寫優先序：較舊 cutoff 不得覆寫較新、同一 cutoff 下 complete 優先於
partial、完整度相同才比 generated_at。這些是條件式寫入，測試因此直接打真實條件運算式。
"""

import importlib

import boto3
import pytest
from moto import mock_aws

SUMMARIES_TABLE = "daily-summaries-test"
CONVERSATIONS_TABLE = "conversations-test"

ELDER = "eld_a1b2c3d4e5f6"
DATE = "2026-07-26"


@pytest.fixture
def db(monkeypatch):
    """在 moto 環境下重新載入 db 模組，讓表名與全域連線都指向假環境。"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_DAILY_SUMMARIES", SUMMARIES_TABLE)
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)

    with mock_aws():
        resource = boto3.resource("dynamodb")
        resource.create_table(
            TableName=SUMMARIES_TABLE,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "date", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "date", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=CONVERSATIONS_TABLE,
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

    importlib.reload(importlib.import_module("src.shared.db"))


def make_summary(**overrides):
    payload = {
        "elder_id": ELDER,
        "date": DATE,
        "overview": "三餐正常並按時服藥",
        "sections": {"diet": "三餐正常", "medication": "血壓藥已服用"},
        "routines": {"completed": 1, "missed": 0, "items": []},
        "alerts": [],
        "interaction_count": 6,
        "data_status": "partial",
        "pending_session_count": 1,
        "input_through_at": "2026-07-26T20:00:00.000+08:00",
        "generated_at": "2026-07-26T20:00:12.000+08:00",
        "generator_version": "summary-generator-1",
    }
    payload.update(overrides)
    return payload


# -- 覆寫優先序 ---------------------------------------------------------------


def test_first_write_stores_completeness_rank(db):
    stored, written = db.put_daily_summary(make_summary())
    assert written is True
    assert stored["completeness_rank"] == 0
    assert stored["schema_version"] == 1
    assert db.get_daily_summary(ELDER, DATE)["overview"] == "三餐正常並按時服藥"


def test_newer_cutoff_overwrites(db):
    db.put_daily_summary(make_summary())
    stored, written = db.put_daily_summary(
        make_summary(
            overview="晚間更新",
            input_through_at="2026-07-26T23:59:59.999+08:00",
            generated_at="2026-07-27T00:00:05.000+08:00",
        )
    )
    assert written is True
    assert stored["overview"] == "晚間更新"


def test_older_cutoff_does_not_overwrite(db):
    db.put_daily_summary(
        make_summary(
            overview="晚間版本",
            input_through_at="2026-07-26T23:59:59.999+08:00",
            generated_at="2026-07-27T00:00:05.000+08:00",
        )
    )
    stored, written = db.put_daily_summary(make_summary(overview="遲到的舊版本"))
    assert written is False
    assert stored["overview"] == "晚間版本"


def test_complete_beats_partial_at_same_cutoff(db):
    db.put_daily_summary(make_summary())
    stored, written = db.put_daily_summary(
        make_summary(
            overview="batch 完成後重算",
            data_status="complete",
            pending_session_count=0,
            generated_at="2026-07-26T21:00:00.000+08:00",
        )
    )
    assert written is True
    assert stored["data_status"] == "complete"
    assert stored["completeness_rank"] == 1


def test_partial_cannot_overwrite_complete_at_same_cutoff(db):
    db.put_daily_summary(
        make_summary(
            overview="完整版本",
            data_status="complete",
            pending_session_count=0,
            generated_at="2026-07-26T21:00:00.000+08:00",
        )
    )
    stored, written = db.put_daily_summary(
        make_summary(overview="手動 partial", generated_at="2026-07-26T22:00:00.000+08:00")
    )
    assert written is False
    assert stored["data_status"] == "complete"
    assert stored["overview"] == "完整版本"


def test_same_cutoff_and_completeness_uses_generated_at(db):
    db.put_daily_summary(make_summary(generated_at="2026-07-26T21:00:00.000+08:00"))

    stale, written = db.put_daily_summary(
        make_summary(overview="更早生成", generated_at="2026-07-26T20:30:00.000+08:00")
    )
    assert written is False
    assert stale["overview"] == "三餐正常並按時服藥"

    fresh, written = db.put_daily_summary(
        make_summary(overview="更晚生成", generated_at="2026-07-26T21:30:00.000+08:00")
    )
    assert written is True
    assert fresh["overview"] == "更晚生成"


@pytest.mark.parametrize("missing", ["elder_id", "date", "input_through_at", "generated_at", "data_status"])
def test_put_requires_identity_and_ordering_fields(db, missing):
    payload = {key: value for key, value in make_summary().items() if key != missing}
    with pytest.raises(db.DBError, match=missing):
        db.put_daily_summary(payload)


# -- 列表查詢 -----------------------------------------------------------------


def test_list_returns_newest_first_within_range(db):
    for date in ("2026-07-24", "2026-07-25", "2026-07-26"):
        db.put_daily_summary(make_summary(date=date))

    items, next_token = db.list_daily_summaries(ELDER, "2026-07-24", "2026-07-26")
    assert [item["date"] for item in items] == ["2026-07-26", "2026-07-25", "2026-07-24"]
    assert next_token is None


def test_list_excludes_dates_outside_range(db):
    db.put_daily_summary(make_summary(date="2026-07-20"))
    db.put_daily_summary(make_summary(date="2026-07-26"))
    items, _ = db.list_daily_summaries(ELDER, "2026-07-25", "2026-07-27")
    assert [item["date"] for item in items] == ["2026-07-26"]


def test_list_pages_with_opaque_token(db):
    for day in range(21, 27):
        db.put_daily_summary(make_summary(date=f"2026-07-{day}"))

    first, token = db.list_daily_summaries(ELDER, "2026-07-21", "2026-07-26", limit=2)
    assert [item["date"] for item in first] == ["2026-07-26", "2026-07-25"]
    assert token

    second, _ = db.list_daily_summaries(
        ELDER, "2026-07-21", "2026-07-26", limit=2, next_token=token
    )
    assert [item["date"] for item in second] == ["2026-07-24", "2026-07-23"]


def test_list_rejects_broken_token(db):
    with pytest.raises(db.DBError, match="next_token"):
        db.list_daily_summaries(ELDER, "2026-07-21", "2026-07-26", next_token="not-base64!!")


# -- 當日對話 -----------------------------------------------------------------


def put_turn(db, conversation_id: str, created_at: str, session_id: str, status: str = "completed"):
    db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS).put_item(
        Item={
            "elder_id": ELDER,
            "record_id": f"TURN#{conversation_id}",
            "item_type": "conversation",
            "conversation_id": conversation_id,
            "conversation_time_key": f"{created_at}#{conversation_id}",
            "created_at": created_at,
            "session_id": session_id,
            "request_status": status,
        }
    )


def test_list_turns_by_day_respects_taipei_day_boundaries(db):
    put_turn(db, "cnv_1", "2026-07-25T23:59:59.999+08:00", "ses_a")
    put_turn(db, "cnv_2", "2026-07-26T00:00:00.000+08:00", "ses_b")
    put_turn(db, "cnv_3", "2026-07-26T23:59:59.999+08:00", "ses_b")
    put_turn(db, "cnv_4", "2026-07-27T00:00:00.000+08:00", "ses_c")

    turns = db.list_turns_by_day(ELDER, DATE)
    assert [turn["conversation_id"] for turn in turns] == ["cnv_2", "cnv_3"]


def test_list_turns_by_day_follows_pagination(db):
    for index in range(5):
        put_turn(db, f"cnv_{index}", f"2026-07-26T09:0{index}:00.000+08:00", "ses_a")

    turns = db.list_turns_by_day(ELDER, DATE, page_size=2)
    assert len(turns) == 5
    assert [turn["conversation_id"] for turn in turns] == [f"cnv_{i}" for i in range(5)]
