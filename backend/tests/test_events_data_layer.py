"""events 資料層測試（moto）。

對應 docs/framework.md 的 Verification 條目：conditional Put 冪等、內容互斥不靜默覆寫、
safety event revision enrichment、時間軸走 GSI 且日界正確、分頁穩定、Decimal 轉換。
"""

from decimal import Decimal
import importlib
import os

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "events-test"


@pytest.fixture
def db(monkeypatch):
    """在 moto 環境下重新載入 db 模組，讓表名與全域連線都指向假環境。"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_EVENTS", TABLE_NAME)

    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "event_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "event_id", "AttributeType": "S"},
                {"AttributeName": "event_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "events-by-time",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "event_time_key", "KeyType": "RANGE"},
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


ELDER = "eld_a1b2c3d4e5f6"


def make_event(db, **overrides):
    payload = {
        "elder_id": ELDER,
        "canonical_event_key": "2026-07-26#SLOT_0900#長者#服用血壓藥",
        "ts": "2026-07-26T09:05:00+08:00",
        "type": "medication",
        "detail": "早餐後服用血壓藥一顆",
        "concept_id": "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication",
        "taxonomy_version": "uco-1.0.0",
    }
    payload.update(overrides)
    return db.create_event(payload)


# -- 寫入與冪等 ---------------------------------------------------------------


def test_create_event_derives_identity_and_normalizes_ts(db):
    event = make_event(db)
    assert event["event_id"] == "evt_" + event["event_id"].split("_")[1]
    assert event["ts"] == "2026-07-26T09:05:00.000+08:00"
    assert event["event_time_key"] == f"{event['ts']}#{event['event_id']}"
    assert event["revision"] == 1
    assert event["schema_version"] == 1
    assert event["extraction_track"] == "batch"
    assert event["created_at"] == event["updated_at"]


def test_create_event_requires_canonical_key_and_ts(db):
    with pytest.raises(db.DBError, match="canonical_event_key"):
        db.create_event({"elder_id": ELDER, "ts": "2026-07-26T09:05:00+08:00", "type": "diet", "detail": "x"})
    with pytest.raises(db.DBError, match="ts"):
        db.create_event(
            {"elder_id": ELDER, "canonical_event_key": "k", "type": "diet", "detail": "x"}
        )


def test_create_event_is_idempotent_for_identical_retry(db):
    first = make_event(db)
    second = make_event(db)
    assert first["event_id"] == second["event_id"]
    assert second["revision"] == 1

    items, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    assert len(items) == 1


def test_create_event_tolerates_non_material_differences(db):
    """structured_detail 與 confidence 屬可補充資訊，模型輸出微小差異不該讓 batch 失敗。"""
    make_event(db, structured_detail={"pill_count": 1}, confidence=0.9)
    retried = make_event(db, structured_detail={"pill_count": 1, "dosage": "一顆"}, confidence=0.7)
    # 既有資料保留不動
    assert retried["structured_detail"] == {"pill_count": 1}
    assert retried["confidence"] == 0.9


def test_same_slot_different_ts_keeps_first_write(db):
    """canonical key 已固定日期與 Slot，Slot 內時間不同不算矛盾，先寫者為準。"""
    first = make_event(db, ts="2026-07-26T09:05:00+08:00")
    again = make_event(db, ts="2026-07-26T09:29:00+08:00")
    assert again["event_id"] == first["event_id"]
    assert again["ts"] == "2026-07-26T09:05:00.000+08:00"


def test_create_event_raises_on_material_conflict(db):
    make_event(db)
    with pytest.raises(db.EventConflictError) as exc_info:
        make_event(db, detail="改成完全不同的描述", type="diet")
    conflict = exc_info.value
    assert "detail" in conflict.differences
    assert "type" in conflict.differences

    # 既有資料未被覆寫
    stored = db.get_event(ELDER, conflict.event_id)
    assert stored["detail"] == "早餐後服用血壓藥一顆"
    assert stored["type"] == "medication"


def test_floats_are_stored_as_decimal_and_none_stripped(db):
    event = make_event(
        db,
        confidence=0.875,
        structured_detail={"systolic_bp": 135.0, "diastolic_bp": None},
        conversation_id=None,
    )
    assert "diastolic_bp" not in event["structured_detail"]
    assert "conversation_id" not in event

    raw = boto3.resource("dynamodb").Table(TABLE_NAME).get_item(
        Key={"elder_id": ELDER, "event_id": event["event_id"]}
    )["Item"]
    assert isinstance(raw["confidence"], Decimal)
    assert isinstance(raw["structured_detail"]["systolic_bp"], Decimal)
    # 讀回時轉成原生型別供業務邏輯使用
    assert db.get_event(ELDER, event["event_id"])["confidence"] == 0.875


# -- enrichment ---------------------------------------------------------------


def test_enrich_event_increments_revision(db):
    event = make_event(db, extraction_track="realtime", type="safety", detail="疑似跌倒")
    enriched = db.enrich_event(
        ELDER,
        event["event_id"],
        expected_revision=1,
        updates={
            "detail": "在浴室滑倒，沒有受傷",
            "evidence_conversation_ids": ["cnv_1", "cnv_2"],
            "confidence": 0.95,
        },
    )
    assert enriched["revision"] == 2
    assert enriched["detail"] == "在浴室滑倒，沒有受傷"
    assert enriched["evidence_conversation_ids"] == ["cnv_1", "cnv_2"]
    # event_id 不變，代表跨軌收斂到同一筆事件
    assert enriched["event_id"] == event["event_id"]
    assert enriched["updated_at"] >= event["updated_at"]


def test_enrich_event_rejects_stale_revision(db):
    event = make_event(db)
    db.enrich_event(ELDER, event["event_id"], 1, {"confidence": 0.5})
    with pytest.raises(db.EventRevisionConflictError):
        db.enrich_event(ELDER, event["event_id"], 1, {"confidence": 0.6})


def test_enrich_event_rejects_identity_and_fact_fields(db):
    event = make_event(db)
    for illegal in ("type", "ts", "canonical_event_key", "event_id", "extraction_track"):
        with pytest.raises(db.DBError, match="不得修改"):
            db.enrich_event(ELDER, event["event_id"], 1, {illegal: "x"})


def test_enrich_event_requires_existing_event(db):
    with pytest.raises(db.EventRevisionConflictError):
        db.enrich_event(ELDER, "evt_doesnotexist", 1, {"confidence": 0.5})


# -- 查詢 ---------------------------------------------------------------------


def seed_timeline(db):
    make_event(
        db,
        canonical_event_key="2026-07-25#SLOT_0800#長者#吃早餐",
        ts="2026-07-25T08:00:00+08:00",
        type="diet",
        detail="吃了稀飯",
    )
    make_event(
        db,
        canonical_event_key="2026-07-26#SLOT_0900#長者#服用血壓藥",
        ts="2026-07-26T09:05:00+08:00",
        type="medication",
        detail="早餐後服用血壓藥一顆",
    )
    # 落在當日最後一毫秒，用來驗證查詢上界
    make_event(
        db,
        canonical_event_key="2026-07-26#SLOT_2330#長者#睡眠品質不佳",
        ts="2026-07-26T23:59:59.999+08:00",
        type="sleep",
        detail="睡得不好",
    )
    make_event(
        db,
        canonical_event_key="2026-07-27#SLOT_0800#長者#量血壓",
        ts="2026-07-27T08:00:00+08:00",
        type="wellbeing",
        detail="血壓 135/85",
    )


def test_list_events_returns_newest_first_within_range(db):
    seed_timeline(db)
    items, next_token = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    assert [item["detail"] for item in items] == ["睡得不好", "早餐後服用血壓藥一顆"]
    assert next_token is None


def test_list_events_includes_last_millisecond_of_range(db):
    """23:59:59.999 的事件必須包含在內；event_time_key 尾端還有 event_id。"""
    seed_timeline(db)
    items, _ = db.list_events(ELDER, "2026-07-25", "2026-07-26")
    assert "睡得不好" in [item["detail"] for item in items]
    assert "血壓 135/85" not in [item["detail"] for item in items]


def test_list_events_filters_by_type(db):
    seed_timeline(db)
    items, _ = db.list_events(ELDER, "2026-07-25", "2026-07-27", event_type="diet")
    assert [item["type"] for item in items] == ["diet"]


def test_list_events_pagination_is_stable(db):
    seed_timeline(db)
    first_page, token = db.list_events(ELDER, "2026-07-25", "2026-07-27", limit=2)
    assert len(first_page) == 2
    assert token

    second_page, _ = db.list_events(ELDER, "2026-07-25", "2026-07-27", limit=2, next_token=token)
    first_ids = [item["event_id"] for item in first_page]
    second_ids = [item["event_id"] for item in second_page]
    assert set(first_ids).isdisjoint(second_ids)
    # 合併後仍是時間遞減且不重複
    all_ts = [item["ts"] for item in first_page + second_page]
    assert all_ts == sorted(all_ts, reverse=True)


def test_list_events_scoped_to_elder(db):
    seed_timeline(db)
    make_event(db, elder_id="eld_ffffffffffff", detail="別人的事件")
    items, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    assert all(item["elder_id"] == ELDER for item in items)


def test_invalid_next_token(db):
    with pytest.raises(db.DBError, match="next_token"):
        db.list_events(ELDER, "2026-07-26", "2026-07-26", next_token="not-base64!!")


# -- routine completion -------------------------------------------------------


def test_routine_completion_converges_across_entries(db):
    """對話完成與照護者手動完成必須收斂到同一筆 canonical completion event。"""
    from_conversation = db.complete_routine_with_event(
        elder_id=ELDER,
        routine_id="rtn_001",
        routine_date="2026-07-26",
        ts="2026-07-26T09:05:00+08:00",
        completed_by="conversation",
        detail="對話中確認已服藥",
        event_type="medication",
        routine_version=1,
        conversation_id="cnv_1",
        extraction_track="realtime",
    )
    from_caregiver = db.complete_routine_with_event(
        elder_id=ELDER,
        routine_id="rtn_001",
        routine_date="2026-07-26",
        ts="2026-07-26T09:20:00+08:00",
        completed_by="caregiver",
        detail="對話中確認已服藥",
        event_type="medication",
        # 同日改版：version 只記錄採用的版本，不參與 identity
        routine_version=2,
    )
    assert from_conversation["event_id"] == from_caregiver["event_id"]
    assert from_caregiver["status"] == "done"

    items, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    completions = [item for item in items if item.get("routine_id") == "rtn_001"]
    assert len(completions) == 1
    assert completions[0]["routine_version"] == 1


def test_batch_cannot_write_routine_completion(db):
    """規範明文：batch 不得建立、修改、停用或完成 routine。"""
    with pytest.raises(db.DBError, match="batch 不得"):
        db.complete_routine_with_event(
            elder_id=ELDER,
            routine_id="rtn_001",
            routine_date="2026-07-26",
            ts="2026-07-26T09:05:00+08:00",
            completed_by="conversation",
            extraction_track="batch",
        )
