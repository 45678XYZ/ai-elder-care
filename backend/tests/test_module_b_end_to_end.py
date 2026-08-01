"""Module B 端到端：close → SQS → batch → GET /events。

這支測試就是 Task 12 的驗收 demo：照護者端看到的一般生活事件，必須是「session 明確關閉、
batch materialize 完成」之後才出現，而且重跑不會產生第二筆。模型呼叫以假 client 取代，
其餘（DynamoDB 條件式寫入、session 狀態機、SQS 訊息內容、API 投影）都是真的。
"""

import importlib
import json

import boto3
import pytest
from moto import mock_aws

from src.extraction.pipeline import ExtractionConfig
from src.extraction.canonical import load_predicate_lexicon
from src.extraction.pipeline import DirectSevenPipeline
from src.extraction.taxonomy import load_taxonomy
from tests.conftest import FakeConverseClient, StubEmbeddingProvider

CONVERSATIONS_TABLE = "conversations-e2e"
EVENTS_TABLE = "events-e2e"
ELDERS_TABLE = "elders-e2e"
ELDER = "eld_a1b2c3d4e5f6"
SESSION = "ses_01J8E2E"


SCRIPT = [
    ("AI", "阿嬤早安，今天有量血壓嗎？"),
    ("長者", "有啊，早上量了，135 跟 85。"),
    ("AI", "血壓藥吃了嗎？"),
    ("長者", "吃了，早餐後吃一顆。"),
]


class FakeSqs:
    """假 SQS：把訊息留在記憶體，測試再手動餵給 consumer。"""

    def __init__(self):
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(json.loads(kwargs["MessageBody"]))
        return {"MessageId": f"m{len(self.messages)}"}

    def as_records(self):
        return [
            {"messageId": f"m{index}", "body": json.dumps(message, ensure_ascii=False)}
            for index, message in enumerate(self.messages)
        ]


class FakeContext:
    aws_request_id = "e2e-worker"


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)
    monkeypatch.setenv("TABLE_EVENTS", EVENTS_TABLE)
    monkeypatch.setenv("TABLE_ELDERS", ELDERS_TABLE)
    monkeypatch.setenv("BATCH_QUEUE_URL", "https://sqs.local/batch")

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
        closer = importlib.reload(importlib.import_module("src.handlers.session_closer"))
        batch = importlib.reload(importlib.import_module("src.handlers.batch_extractor"))
        events_api = importlib.reload(importlib.import_module("src.handlers.events"))
        yield db, sessions, closer, batch, events_api

    for name in (
        "src.shared.db",
        "src.shared.sessions",
        "src.handlers.session_closer",
        "src.handlers.batch_extractor",
        "src.handlers.events",
    ):
        importlib.reload(importlib.import_module(name))


def seed_conversation(sessions):
    table = boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE)
    turn_ids = []
    for index, (speaker, text) in enumerate(SCRIPT):
        turn_id = f"cnv_{index:03d}"
        turn_ids.append(turn_id)
        item = {
            "elder_id": ELDER,
            "record_id": f"TURN#{turn_id}",
            "item_type": "conversation",
            "conversation_id": turn_id,
            "session_id": SESSION,
            "created_at": f"2026-07-26T09:{index * 2:02d}:00.000+08:00",
            "request_status": "completed",
        }
        if speaker == "長者":
            item["elder_transcript"] = text
        else:
            item["ai_respond_text"] = text
        table.put_item(Item=item)

    boto3.resource("dynamodb").Table(ELDERS_TABLE).put_item(
        Item={
            "elder_id": ELDER,
            "name": "陳阿蘭",
            "nickname": "阿蘭嬤",
            "health_notes": ["高血壓"],
            "caregiver_ids": ["caregiver-sub"],
        }
    )

    sessions.put_session(
        {
            "elder_id": ELDER,
            "session_id": SESSION,
            "state": sessions.STATE_ACTIVE,
            "turn_ids": turn_ids,
            "turn_count": len(turn_ids),
            "inflight_turn_ids": [],
            "inflight_turn_count": 0,
            "input_bytes": 0,
            "started_at": "2026-07-26T09:00:00.000+08:00",
            "last_activity_at": "2026-07-26T09:08:00.000+08:00",
            "session_state_key": sessions.STATE_KEY_ACTIVE,
            "session_state_time_key": f"2026-07-26T09:08:00.000+08:00#{ELDER}#{SESSION}",
        }
    )
    return turn_ids


def build_pipeline(model_client):
    taxonomy = load_taxonomy()
    lexicon = load_predicate_lexicon()
    embedder = StubEmbeddingProvider()
    return DirectSevenPipeline(
        config=ExtractionConfig(event_slot_minutes=30),
        taxonomy=taxonomy,
        lexicon=lexicon,
        client=model_client,
        embedder=embedder,
    )


def model_responses():
    """direct_seven: 單次萃取呼叫。"""
    return [
        json.dumps(
            {
                "unit_id": "batch-0",
                "reference_datetime": "2026-07-26T09:08:00.000+08:00",
                "events": [
                    {
                        "event_index": 0,
                        "high_level_type": "medication",
                        "subject": "長者",
                        "predicate": "吃血壓藥",
                        "event_summary": "早餐後服用血壓藥一顆",
                        "raw_temporal_expression": "早上",
                        "observed_at": "2026-07-26T08:00:00.000+08:00",
                        "confidence_score": 0.85,
                    }
                ],
            },
            ensure_ascii=False,
        )
    ]


def caregiver_events_request():
    return {
        "httpMethod": "GET",
        "queryStringParameters": {"elder_id": ELDER, "from": "2026-07-26", "to": "2026-07-26"},
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }


def test_close_to_events_end_to_end(stack):
    db, sessions, closer, batch, events_api = stack
    seed_conversation(sessions)
    sqs = FakeSqs()

    # 1. batch 之前照護者端看不到一般生活事件
    before = json.loads(events_api.handler(caregiver_events_request(), None)["body"])
    assert before["items"] == []

    # 2. 長者端明確 close
    close_response = closer.handle_close_request(
        {
            "pathParameters": {"session_id": SESSION},
            "requestContext": {
                "authorizer": {"claims": {"sub": "elder-sub", "elder_id": ELDER}}
            },
        },
        sqs_client=sqs,
    )
    assert close_response["statusCode"] == 200
    assert json.loads(close_response["body"])["batch_status"] == "pending"
    assert len(sqs.messages) == 1

    # 3. batch consumer 處理佇列訊息
    pipeline = build_pipeline(FakeConverseClient(model_responses()))
    for record in sqs.as_records():
        assert batch.process_record(record, context=FakeContext(), pipeline=pipeline) == (
            sessions.CLAIM_ACQUIRED
        )

    session = sessions.get_session(ELDER, SESSION)
    assert session["state"] == sessions.STATE_CLOSED
    assert session["batch_status"] == sessions.BATCH_COMPLETED
    # direct_seven pipeline 不產生 chunk_manifest

    # 4. 照護者端看到一般生活事件，且不含 extraction internals
    after = json.loads(events_api.handler(caregiver_events_request(), None)["body"])
    assert len(after["items"]) >= 1
    item = after["items"][0]
    assert item["type"] == "medication"
    assert item["detail"] == "早餐後服用血壓藥一顆"
    assert item["source"] == "conversation"
    for internal in ("canonical_event_key", "concept_id", "structured_detail", "revision"):
        assert internal not in item

    # 5. 內部欄位仍完整寫入，供摘要與統計使用
    stored = db.get_event(ELDER, item["event_id"])
    assert stored["concept_id"] == "UCO.HighLevel.medication"
    assert stored["taxonomy_version"] == "uco-1.0.0"
    assert stored["extraction_track"] == "batch"
    assert stored["evidence_conversation_ids"]


def test_duplicate_delivery_does_not_duplicate_events(stack):
    """SQS 至少一次投遞：同一則訊息重投不得產生第二筆事件。"""
    db, sessions, closer, batch, _ = stack
    seed_conversation(sessions)
    sqs = FakeSqs()
    closer.handle_close_request(
        {
            "pathParameters": {"session_id": SESSION},
            "requestContext": {
                "authorizer": {"claims": {"sub": "elder-sub", "elder_id": ELDER}}
            },
        },
        sqs_client=sqs,
    )

    record = sqs.as_records()[0]
    pipeline = build_pipeline(FakeConverseClient(model_responses()))
    first = batch.process_record(record, context=FakeContext(), pipeline=pipeline)
    second = batch.process_record(record, context=FakeContext(), pipeline=pipeline)

    assert first == sessions.CLAIM_ACQUIRED
    assert second == sessions.CLAIM_ALREADY_COMPLETED

    events, _ = db.list_events(ELDER, "2026-07-26", "2026-07-26", limit=50)
    assert len({event["event_id"] for event in events}) == len(events)


def test_events_are_not_visible_before_batch_completes(stack):
    """一般事件只在 close 且 batch materialize 後出現（framework Verification）。"""
    db, sessions, closer, _, events_api = stack
    seed_conversation(sessions)
    closer.handle_close_request(
        {
            "pathParameters": {"session_id": SESSION},
            "requestContext": {
                "authorizer": {"claims": {"sub": "elder-sub", "elder_id": ELDER}}
            },
        },
        sqs_client=FakeSqs(),
    )
    body = json.loads(events_api.handler(caregiver_events_request(), None)["body"])
    assert body["items"] == []
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_PENDING
