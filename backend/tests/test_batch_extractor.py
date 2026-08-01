"""batch extractor handler 測試。

對應 framework Verification：lease-expired 接管、lease 有效的 duplicate 不執行直接 ack、
`failed` 不得 claim、events 寫入冪等、permanent 設 failed／retryable 交回重投。
"""

import importlib
import json

import boto3
import pytest
from moto import mock_aws

from src.extraction.models import CanonicalEvent
from src.extraction.pipeline import PipelineResult
from src.shared import bedrock

CONVERSATIONS_TABLE = "conversations-test"
EVENTS_TABLE = "events-test"
ELDERS_TABLE = "elders-test"
ELDER = "eld_a1b2c3d4e5f6"
SESSION = "ses_01J8"
TURN_IDS = ["cnv_001", "cnv_002"]
INPUT_BYTES = 256



class FakeContext:
    aws_request_id = "worker-1"


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)
    monkeypatch.setenv("TABLE_EVENTS", EVENTS_TABLE)
    monkeypatch.setenv("TABLE_ELDERS", ELDERS_TABLE)

    with mock_aws():
        resource = boto3.resource("dynamodb")
        resource.create_table(
            TableName=CONVERSATIONS_TABLE,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "record_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "record_id", "AttributeType": "S"},
                {"AttributeName": "session_state_key", "AttributeType": "S"},
                {"AttributeName": "session_state_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "sessions-by-state",
                    "KeySchema": [
                        {"AttributeName": "session_state_key", "KeyType": "HASH"},
                        {"AttributeName": "session_state_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=EVENTS_TABLE,
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
        resource.create_table(
            TableName=ELDERS_TABLE,
            KeySchema=[{"AttributeName": "elder_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "elder_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        db = importlib.reload(importlib.import_module("src.shared.db"))
        sessions = importlib.reload(importlib.import_module("src.shared.sessions"))
        handler_module = importlib.reload(importlib.import_module("src.handlers.batch_extractor"))
        yield db, sessions, handler_module

    importlib.reload(importlib.import_module("src.shared.db"))
    importlib.reload(importlib.import_module("src.shared.sessions"))
    importlib.reload(importlib.import_module("src.handlers.batch_extractor"))


def seed_closed_session(sessions):
    table = boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE)
    for index, turn_id in enumerate(TURN_IDS):
        table.put_item(
            Item={
                "elder_id": ELDER,
                "record_id": f"TURN#{turn_id}",
                "item_type": "conversation",
                "conversation_id": turn_id,
                "created_at": f"2026-07-26T09:{index:02d}:00.000+08:00",
                "elder_transcript": f"第 {index} 句：血壓藥吃了",
            }
        )

    session = {
        "elder_id": ELDER,
        "session_id": SESSION,
        "state": sessions.STATE_ACTIVE,
        "turn_ids": TURN_IDS,
        "turn_count": len(TURN_IDS),
        "inflight_turn_ids": [],
        "inflight_turn_count": 0,
        "input_bytes": INPUT_BYTES,
        "started_at": "2026-07-26T09:00:00.000+08:00",
        "last_activity_at": "2026-07-26T09:05:00.000+08:00",
    }
    sessions.put_session(session)
    sessions.begin_closing(ELDER, SESSION, close_reason="client_requested")
    return sessions.finalize_closed(
        ELDER, SESSION, turn_ids=TURN_IDS, input_bytes=INPUT_BYTES
    )


def make_record(snapshot_hash, message_id="msg-1"):
    return {
        "messageId": message_id,
        "body": json.dumps(
            {
                "elder_id": ELDER,
                "session_id": SESSION,
                "session_snapshot_hash": snapshot_hash,
            }
        ),
    }


def canonical_event(event_id="evt_aaaaaaaaaaaa", key="2026-07-26#SLOT_0900#長者#服用血壓藥"):
    return CanonicalEvent(
        elder_id=ELDER,
        event_id=event_id,
        canonical_event_key=key,
        ts="2026-07-26T09:05:00.000+08:00",
        type="medication",
        taxonomy_version="uco-1.0.0",
        subject="長者",
        predicate="服用血壓藥",
        detail="吃了血壓藥",
        structured_detail={"medication_item": "血壓藥"},
        confidence=0.9,
        session_id=SESSION,
        source_chunk_id="chk_a",
        conversation_id="cnv_001",
        evidence_conversation_ids=("cnv_001", "cnv_002"),
    )


class FakePipeline:
    """假 pipeline：只回固定結果，讓 handler 的狀態機邏輯可獨立測試。"""

    def __init__(self, events=None, error=None):
        self.events = tuple(events or (canonical_event(),))
        self.error = error
        self.run_calls = 0

    def run(self, elder_id, session_id, snapshot_hash, turns, *, elder=None):
        self.run_calls += 1
        if self.error:
            raise self.error
        return PipelineResult(
            session_id=session_id,
            pipeline_name="direct_seven",
            events=self.events,
        )


# -- 正常路徑 -----------------------------------------------------------------


def test_successful_batch_writes_events_and_completes(stack):
    db, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    pipeline = FakePipeline()

    outcome = handler_module.process_record(
        make_record(closed["session_snapshot_hash"]), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_ACQUIRED

    session = sessions.get_session(ELDER, SESSION)
    assert session["batch_status"] == sessions.BATCH_COMPLETED
    assert "session_state_key" not in session
    assert "batch_lease_owner" not in session

    events, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    assert len(events) == 1
    assert events[0]["type"] == "medication"
    assert events[0]["extraction_track"] == "batch"



def test_pipeline_run_is_called_on_first_run(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    pipeline = FakePipeline()

    handler_module.process_record(
        make_record(closed["session_snapshot_hash"]), context=FakeContext(), pipeline=pipeline
    )
    assert pipeline.run_calls == 1


def test_event_writes_are_idempotent_across_retries(stack):
    db, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]

    handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=FakePipeline()
    )
    # 模擬 replay：把 batch 狀態退回 pending 後重跑同一份 snapshot
    boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE).update_item(
        Key={"elder_id": ELDER, "record_id": f"SESSION#{SESSION}"},
        UpdateExpression="SET batch_status = :pending",
        ExpressionAttributeValues={":pending": sessions.BATCH_PENDING},
    )
    handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=FakePipeline()
    )

    events, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26")
    assert len(events) == 1


# -- 重複投遞的四種處置 -------------------------------------------------------


def test_duplicate_with_active_lease_is_acked_without_running(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="other-worker")

    pipeline = FakePipeline()
    outcome = handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_LEASE_ACTIVE
    assert pipeline.run_calls == 0


def test_completed_duplicate_is_acked_without_running(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]
    handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=FakePipeline()
    )

    pipeline = FakePipeline()
    outcome = handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_ALREADY_COMPLETED
    assert pipeline.run_calls == 0


def test_failed_session_is_not_reclaimed(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="w0")
    sessions.fail_batch(ELDER, SESSION, owner="w0", code="X", message="boom")

    pipeline = FakePipeline()
    outcome = handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_ALREADY_FAILED
    assert pipeline.run_calls == 0


def test_stale_snapshot_hash_is_acked(stack):
    _, sessions, handler_module = stack
    seed_closed_session(sessions)
    pipeline = FakePipeline()
    outcome = handler_module.process_record(
        make_record("stale"), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_SNAPSHOT_MISMATCH
    assert pipeline.run_calls == 0


def test_expired_lease_is_taken_over(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="dead-worker", lease_seconds=-60
    )

    pipeline = FakePipeline()
    outcome = handler_module.process_record(
        make_record(hash_value), context=FakeContext(), pipeline=pipeline
    )
    assert outcome == sessions.CLAIM_ACQUIRED
    assert pipeline.run_calls == 1
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_COMPLETED


# -- 失敗處理 -----------------------------------------------------------------


def test_retryable_failure_releases_lease_and_reraises(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    pipeline = FakePipeline(error=bedrock.RetryableBedrockError("throttled"))

    with pytest.raises(bedrock.RetryableBedrockError):
        handler_module.process_record(
            make_record(closed["session_snapshot_hash"]), context=FakeContext(), pipeline=pipeline
        )

    session = sessions.get_session(ELDER, SESSION)
    # 回到 pending 讓重投或 recovery sweep 立刻能接手
    assert session["batch_status"] == sessions.BATCH_PENDING
    assert "batch_lease_owner" not in session


def test_permanent_model_failure_sets_failed(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    pipeline = FakePipeline(error=bedrock.PermanentBedrockError("AccessDenied"))

    with pytest.raises(handler_module.PermanentBatchError):
        handler_module.process_record(
            make_record(closed["session_snapshot_hash"]), context=FakeContext(), pipeline=pipeline
        )

    session = sessions.get_session(ELDER, SESSION)
    assert session["batch_status"] == sessions.BATCH_FAILED
    assert session["batch_error"]["code"] == "MODEL_PERMANENT_ERROR"


def test_event_conflict_sets_failed(stack):
    """既有事件與這次萃取矛盾屬需要人看的問題，不該無限重試。"""
    db, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]

    # 先寫入同一 canonical key 但內容不同的事件
    conflicting = canonical_event().to_event_item()
    conflicting["detail"] = "完全不同的描述"
    db.create_event(conflicting)

    with pytest.raises(handler_module.PermanentBatchError, match="互斥"):
        handler_module.process_record(
            make_record(hash_value), context=FakeContext(), pipeline=FakePipeline()
        )
    session = sessions.get_session(ELDER, SESSION)
    assert session["batch_status"] == sessions.BATCH_FAILED
    assert session["batch_error"]["code"] == "EVENT_CONFLICT"


def test_session_without_turn_ids_is_permanent_failure(stack):
    _, sessions, handler_module = stack
    seed_closed_session(sessions)
    boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE).update_item(
        Key={"elder_id": ELDER, "record_id": f"SESSION#{SESSION}"},
        UpdateExpression="SET turn_ids = :empty",
        ExpressionAttributeValues={":empty": []},
    )
    session = sessions.get_session(ELDER, SESSION)

    with pytest.raises(handler_module.PermanentBatchError, match="turn_ids"):
        handler_module.process_record(
            make_record(session["session_snapshot_hash"]),
            context=FakeContext(),
            pipeline=FakePipeline(),
        )
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_FAILED


# -- 訊息與 handler 介面 -------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["not json", json.dumps({"elder_id": ELDER}), json.dumps({})],
)
def test_malformed_message_is_permanent(stack, body):
    _, _, handler_module = stack
    with pytest.raises(handler_module.PermanentBatchError):
        handler_module.parse_message({"messageId": "m", "body": body})


def test_handler_reports_partial_batch_failures(stack):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)
    hash_value = closed["session_snapshot_hash"]

    # 第一則 snapshot 不符會直接 ack；第二則格式錯誤屬 permanent，也不重投
    event = {
        "Records": [
            make_record("stale", message_id="msg-ok"),
            {"messageId": "msg-bad", "body": "not json"},
        ]
    }
    response = handler_module.handler(event, FakeContext())
    assert response == {"batchItemFailures": []}


def test_handler_returns_failure_for_retryable(stack, monkeypatch):
    _, sessions, handler_module = stack
    closed = seed_closed_session(sessions)

    def boom(record, *, context=None, pipeline=None):
        raise bedrock.RetryableBedrockError("throttled")

    monkeypatch.setattr(handler_module, "process_record", boom)
    response = handler_module.handler(
        {"Records": [make_record(closed["session_snapshot_hash"], message_id="msg-1")]},
        FakeContext(),
    )
    assert response == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}


def test_turn_speaker_is_derived_from_existing_fields(stack):
    _, _, handler_module = stack
    elder_turn = handler_module._to_turn(
        {
            "conversation_id": "cnv_001",
            "created_at": "2026-07-26T09:00:00.000+08:00",
            "elder_transcript": "我吃過藥了",
            "ai_respond_text": "很好",
        }
    )
    assert elder_turn.speaker == "長者"
    assert elder_turn.text == "長者: 我吃過藥了\nAI: 很好"

    ai_turn = handler_module._to_turn(
        {
            "conversation_id": "cnv_002",
            "created_at": "2026-07-26T09:01:00.000+08:00",
            "ai_prompt_text": "早安，今天吃藥了嗎？",
        }
    )
    assert ai_turn.speaker == "AI"
    assert ai_turn.text == "AI: 早安，今天吃藥了嗎？"

