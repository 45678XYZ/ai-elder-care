"""每日摘要的整合驗收（moto，只有模型是假的）。

驗 docs/feature_daily-summarization.md §12 的對照項：
partial → complete 的重算路徑、較舊或較不完整的結果不得覆寫、routine occurrence 快照與
摘要一致、API 不外流內部欄位、無資料日不呼叫模型。

DynamoDB 條件式寫入、GSI 查詢、handler 投影都是真的；只有 Bedrock 換成假 client。
"""

import importlib
import json

import boto3
import pytest
from moto import mock_aws

from tests.conftest import FakeConverseClient

CONVERSATIONS_TABLE = "conversations-summary-e2e"
EVENTS_TABLE = "events-summary-e2e"
ELDERS_TABLE = "elders-summary-e2e"
ROUTINES_TABLE = "routines-summary-e2e"
SUMMARIES_TABLE = "summaries-summary-e2e"

ELDER = "eld_a1b2c3d4e5f6"
DATE = "2026-07-26"
CAREGIVER = "caregiver-sub"

MODEL_OUTPUT = json.dumps(
    {
        "overview": "早上按時服藥，下午散步半小時。",
        "sections": {
            "diet": None,
            "activity": "下午到公園散步約 30 分鐘",
            "sleep": None,
            "medication": "血壓藥已按時服用",
            "wellbeing": None,
            "safety": None,
            "other": None,
        },
        "alerts": [],
    },
    ensure_ascii=False,
)


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)
    monkeypatch.setenv("TABLE_EVENTS", EVENTS_TABLE)
    monkeypatch.setenv("TABLE_ELDERS", ELDERS_TABLE)
    monkeypatch.setenv("TABLE_ROUTINES", ROUTINES_TABLE)
    monkeypatch.setenv("TABLE_DAILY_SUMMARIES", SUMMARIES_TABLE)

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
        resource.create_table(
            TableName=ROUTINES_TABLE,
            KeySchema=[
                {"AttributeName": "routine_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "routine_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "version_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "routine-versions-by-elder",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "version_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
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

        db = importlib.reload(importlib.import_module("src.shared.db"))
        sessions = importlib.reload(importlib.import_module("src.shared.sessions"))
        routines = importlib.reload(importlib.import_module("src.shared.routines"))
        summarizer = importlib.reload(importlib.import_module("src.shared.summarizer"))
        api = importlib.reload(importlib.import_module("src.handlers.summaries"))
        generator = importlib.reload(importlib.import_module("src.handlers.summary_generator"))

        db.create_elder({"elder_id": ELDER, "name": "陳阿蘭", "caregiver_ids": [CAREGIVER]})
        yield {
            "db": db,
            "sessions": sessions,
            "routines": routines,
            "summarizer": summarizer,
            "api": api,
            "generator": generator,
        }

    for module in (
        "src.shared.db",
        "src.shared.sessions",
        "src.shared.routines",
        "src.shared.summarizer",
        "src.handlers.summaries",
        "src.handlers.summary_generator",
    ):
        importlib.reload(importlib.import_module(module))


def seed_turn(db, conversation_id: str, created_at: str, session_id: str):
    db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS).put_item(
        Item={
            "elder_id": ELDER,
            "record_id": f"TURN#{conversation_id}",
            "item_type": "conversation",
            "conversation_id": conversation_id,
            "conversation_time_key": f"{created_at}#{conversation_id}",
            "created_at": created_at,
            "session_id": session_id,
            "request_status": "completed",
        }
    )


def seed_session(sessions, session_id: str, *, state: str, batch_status: str | None = None):
    session = {
        "elder_id": ELDER,
        "session_id": session_id,
        "state": state,
        "started_at": f"{DATE}T09:00:00.000+08:00",
        "last_activity_at": f"{DATE}T09:30:00.000+08:00",
        "turn_ids": ["cnv_1"],
        "turn_count": 1,
        "inflight_turn_ids": [],
        "inflight_turn_count": 0,
        "input_bytes": 100,
        "recent_conversation_ids": ["cnv_1"],
        "batch_attempts": 0,
    }
    if batch_status:
        session["batch_status"] = batch_status
    sessions.put_session(session)


def seed_routine(db, routine_id: str, *, title: str, time_of_day: str):
    db.get_dynamodb_resource().Table(db.TABLE_ROUTINES).put_item(
        Item={
            "routine_id": routine_id,
            "version": 1,
            "elder_id": ELDER,
            "version_time_key": f"2026-07-01T10:00:00.000+08:00#{routine_id}#1",
            "effective_from": "2026-07-01T10:00:00.000+08:00",
            "is_current": True,
            "active": True,
            "remind": True,
            "title": title,
            "type": "medication",
            "schedule": {"freq": "daily", "time": time_of_day},
        }
    )


def seed_event(db, *, ts: str, event_type: str, detail: str, slot: str):
    return db.create_event(
        {
            "elder_id": ELDER,
            "canonical_event_key": f"{DATE}#{slot}#長者#{detail[:6]}",
            "ts": ts,
            "type": event_type,
            "detail": detail,
        }
    )


def get_summaries(api, **params):
    return api.handler(
        {
            "httpMethod": "GET",
            "queryStringParameters": {"elder_id": ELDER, **params},
            "requestContext": {"authorizer": {"claims": {"sub": CAREGIVER}}},
        },
        None,
    )


def generate_summary(api, body):
    return api.handler(
        {
            "httpMethod": "POST",
            "body": json.dumps(body),
            "requestContext": {"authorizer": {"claims": {"sub": CAREGIVER}}},
        },
        None,
    )


def body_of(response):
    return json.loads(response["body"])


def test_partial_then_complete_after_batch_finishes(stack, monkeypatch):
    db, sessions, summarizer, api = stack["db"], stack["sessions"], stack["summarizer"], stack["api"]

    seed_turn(db, "cnv_1", f"{DATE}T09:05:00.000+08:00", "ses_a")
    seed_session(sessions, "ses_a", state="closed", batch_status="pending")
    seed_routine(db, "rtn_001", title="吃血壓藥", time_of_day="09:00")
    seed_event(db, ts=f"{DATE}T09:05:00+08:00", event_type="medication", detail="早餐後服用血壓藥", slot="SLOT_0900")

    monkeypatch.setattr(
        summarizer.bedrock, "get_runtime_client", lambda: FakeConverseClient(MODEL_OUTPUT)
    )

    # 1. batch 還沒完成 → partial
    first, written = summarizer.generate_and_store(
        ELDER, DATE, input_through_at=f"{DATE}T20:00:00+08:00"
    )
    assert written is True
    assert first["data_status"] == "partial"
    assert first["pending_session_count"] == 1
    assert first["interaction_count"] == 1

    # 2. batch 完成後以較新的 cutoff 重算 → complete
    sessions.put_session(
        {
            **sessions.get_session(ELDER, "ses_a"),
            "batch_status": sessions.BATCH_COMPLETED,
        }
    )
    second, written = summarizer.generate_and_store(
        ELDER, DATE, input_through_at=f"{DATE}T23:59:59.999+08:00"
    )
    assert written is True
    assert second["data_status"] == "complete"
    assert second["pending_session_count"] == 0

    # 3. 較舊 cutoff 的 partial 不得覆寫較新的 complete
    seed_session(sessions, "ses_b", state="active")
    seed_turn(db, "cnv_2", f"{DATE}T21:00:00.000+08:00", "ses_b")
    stale, written = summarizer.generate_and_store(
        ELDER, DATE, input_through_at=f"{DATE}T20:30:00+08:00"
    )
    assert written is False
    assert stale["data_status"] == "complete"

    # 4. API 回最新那份，且不外流內部欄位
    response = get_summaries(api, **{"from": DATE, "to": DATE})
    assert response["statusCode"] == 200
    item = body_of(response)["items"][0]
    assert item["data_status"] == "complete"
    # 一般用藥事件不是 canonical completion event，因此 occurrence 仍是 missed：
    # 完成狀態只由 completion event 決定，摘要不會因為「看起來吃了藥」就宣稱完成
    assert item["routines"] == {
        "completed": 0,
        "missed": 1,
        "items": [{"routine_id": "rtn_001", "title": "吃血壓藥", "status": "missed"}],
    }
    for internal in ("input_through_at", "completeness_rank", "generator_version", "schema_version"):
        assert internal not in response["body"]


def test_routine_snapshot_matches_occurrence_derivation(stack, monkeypatch):
    db, sessions, summarizer, api = stack["db"], stack["sessions"], stack["summarizer"], stack["api"]
    routines = stack["routines"]

    seed_routine(db, "rtn_001", title="吃血壓藥", time_of_day="09:00")
    seed_routine(db, "rtn_002", title="量血壓", time_of_day="19:00")
    db.complete_routine_with_event(
        elder_id=ELDER,
        routine_id="rtn_001",
        routine_date=DATE,
        ts=f"{DATE}T09:05:00+08:00",
        completed_by="conversation",
        routine_version=1,
        event_type="medication",
    )
    monkeypatch.setattr(
        summarizer.bedrock, "get_runtime_client", lambda: FakeConverseClient(MODEL_OUTPUT)
    )

    cutoff = f"{DATE}T23:00:00+08:00"
    occurrences = routines.list_occurrences(ELDER, DATE, cutoff=cutoff, grace=120)
    summary, _ = summarizer.generate_and_store(ELDER, DATE, input_through_at=cutoff)

    assert summary["routines"] == routines.summary_snapshot(occurrences)
    assert summary["routines"]["completed"] == 1
    assert summary["routines"]["missed"] == 1

    response = generate_summary(api, {"elder_id": ELDER, "date": DATE})
    assert response["statusCode"] == 200
    assert body_of(response)["routines"]["completed"] == 1


def test_empty_day_is_complete_and_skips_the_model(stack, monkeypatch):
    summarizer, api = stack["summarizer"], stack["api"]

    fake = FakeConverseClient(MODEL_OUTPUT)
    monkeypatch.setattr(summarizer.bedrock, "get_runtime_client", lambda: fake)

    summary, written = summarizer.generate_and_store(
        ELDER, DATE, input_through_at=f"{DATE}T23:59:59.999+08:00"
    )
    assert written is True
    assert fake.requests == []
    assert summary["data_status"] == "complete"
    assert summary["interaction_count"] == 0
    assert summary["pending_session_count"] == 0
    assert summary["alerts"] == []

    item = body_of(get_summaries(api, **{"from": DATE, "to": DATE}))["items"][0]
    assert all(value is None for value in item["sections"].values())
    assert item["overview"] == summarizer.EMPTY_OVERVIEW


def test_scheduled_backfill_upgrades_partial_to_complete(stack, monkeypatch):
    db, sessions, summarizer = stack["db"], stack["sessions"], stack["summarizer"]
    generator = stack["generator"]

    today = summarizer.day_key(summarizer.datetime.now(summarizer.TZ_TAIPEI))
    seed_turn(db, "cnv_1", f"{today}T09:05:00.000+08:00", "ses_a")
    seed_session(sessions, "ses_a", state="closed", batch_status="pending")
    monkeypatch.setattr(
        summarizer.bedrock, "get_runtime_client", lambda: FakeConverseClient(MODEL_OUTPUT)
    )

    nightly = generator.handler({"mode": "nightly", "date": today}, None)
    assert nightly["partial"] == 1

    # batch 完成後 sweep 應把它補成 complete
    sessions.put_session(
        {**sessions.get_session(ELDER, "ses_a"), "batch_status": sessions.BATCH_COMPLETED}
    )
    backfill = generator.handler({"mode": "backfill"}, None)
    assert backfill["regenerated"] == 1
    assert backfill["upgraded"] == 1
    assert db.get_daily_summary(ELDER, today)["data_status"] == "complete"
