"""session closer 與 DLQ reconciler 測試。

對應 framework Verification：inflight 回 409 `REQUEST_IN_PROGRESS`、closed 後不可追加、
snapshot hash canonical serialization、SQS 派送中斷可由 sweep 補投、
DLQ hash 不符不得誤改 session。
"""

import importlib
import json

import boto3
import pytest
from moto import mock_aws

from src.extraction.temporal import TZ_TAIPEI

CONVERSATIONS_TABLE = "conversations-test"
ELDERS_TABLE = "elders-test"
ELDER = "eld_a1b2c3d4e5f6"
SESSION = "ses_01J8"
TURN_IDS = ["cnv_001", "cnv_002"]


class FakeSqs:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    def send_message(self, **kwargs):
        if self.error:
            raise self.error
        self.messages.append(json.loads(kwargs["MessageBody"]))
        return {"MessageId": "m1"}


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)
        return {"MessageId": "s1"}


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)
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
            TableName=ELDERS_TABLE,
            KeySchema=[{"AttributeName": "elder_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "elder_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        importlib.reload(importlib.import_module("src.shared.db"))
        sessions = importlib.reload(importlib.import_module("src.shared.sessions"))
        closer = importlib.reload(importlib.import_module("src.handlers.session_closer"))
        reconciler = importlib.reload(importlib.import_module("src.handlers.dlq_reconciler"))
        yield sessions, closer, reconciler

    importlib.reload(importlib.import_module("src.shared.db"))
    importlib.reload(importlib.import_module("src.shared.sessions"))
    importlib.reload(importlib.import_module("src.handlers.session_closer"))
    importlib.reload(importlib.import_module("src.handlers.dlq_reconciler"))


def seed_turns(*, statuses=None, session_id=SESSION):
    table = boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE)
    for index, turn_id in enumerate(TURN_IDS):
        item = {
            "elder_id": ELDER,
            "record_id": f"TURN#{turn_id}",
            "item_type": "conversation",
            "conversation_id": turn_id,
            "session_id": session_id,
            "created_at": f"2026-07-26T09:{index:02d}:00.000+08:00",
            "elder_transcript": f"第 {index} 句",
            "request_status": (statuses or ["completed"] * len(TURN_IDS))[index],
        }
        table.put_item(Item=item)


def seed_session(sessions, *, inflight=0, state=None, last_activity="2026-07-26T09:05:00.000+08:00"):
    return sessions.put_session(
        {
            "elder_id": ELDER,
            "session_id": SESSION,
            "state": state or sessions.STATE_ACTIVE,
            "turn_ids": TURN_IDS,
            "turn_count": len(TURN_IDS),
            "inflight_turn_ids": [f"cnv_x{index}" for index in range(inflight)],
            "inflight_turn_count": inflight,
            "input_bytes": 0,
            "started_at": "2026-07-26T09:00:00.000+08:00",
            "last_activity_at": last_activity,
            "session_state_key": sessions.STATE_KEY_ACTIVE,
            "session_state_time_key": f"{last_activity}#{ELDER}#{SESSION}",
        }
    )


def elder_event(session_id=SESSION, elder_id=ELDER):
    return {
        "httpMethod": "POST",
        "pathParameters": {"session_id": session_id},
        "requestContext": {
            "authorizer": {"claims": {"sub": "elder-sub", "elder_id": elder_id}}
        },
    }


def body_of(response):
    return json.loads(response["body"])


# -- close endpoint -----------------------------------------------------------


def test_close_returns_200_and_enqueues_batch(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    sqs = FakeSqs()

    response = closer.handle_close_request(elder_event(), sqs_client=sqs)
    assert response["statusCode"] == 200
    body = body_of(response)
    assert body["session_id"] == SESSION
    assert body["status"] == "closed"
    assert body["batch_status"] == "pending"
    assert body["closed_at"]

    # 派送訊息帶 snapshot hash，consumer 才能判斷 duplicate 與 replay
    assert sqs.messages == [
        {
            "elder_id": ELDER,
            "session_id": SESSION,
            "session_snapshot_hash": sessions.get_session(ELDER, SESSION)[
                "session_snapshot_hash"
            ],
        }
    ]


def test_close_is_idempotent_and_keeps_closed_at(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    sqs = FakeSqs()

    first = body_of(closer.handle_close_request(elder_event(), sqs_client=sqs))
    second = body_of(closer.handle_close_request(elder_event(), sqs_client=sqs))
    # closed_at 由首次 close 決定，重送不改變
    assert first["closed_at"] == second["closed_at"]
    # 仍 pending 時會再投一次：這正好讓「首次 SendMessage 失敗」能靠 App 重試救回。
    # 重複投遞由 consumer 的 lease／狀態機去重，不會重跑萃取。
    assert sqs.messages[0] == sqs.messages[1]


def test_inflight_turn_returns_409(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions, inflight=1)
    response = closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    assert response["statusCode"] == 409
    assert body_of(response)["error"]["code"] == "REQUEST_IN_PROGRESS"


def test_unknown_session_returns_404(stack):
    _, closer, _ = stack
    response = closer.handle_close_request(elder_event(session_id="ses_nope"), sqs_client=FakeSqs())
    assert response["statusCode"] == 404
    assert body_of(response)["error"]["code"] == "SESSION_NOT_FOUND"


def test_other_elders_session_returns_404_not_403(stack):
    """不以 403 區分，避免洩漏 session 是否存在。"""
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    response = closer.handle_close_request(
        elder_event(elder_id="eld_ffffffffffff"), sqs_client=FakeSqs()
    )
    assert response["statusCode"] == 404
    assert body_of(response)["error"]["code"] == "SESSION_NOT_FOUND"


def test_caregiver_cannot_close(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    event = {
        "pathParameters": {"session_id": SESSION},
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }
    response = closer.handle_close_request(event, sqs_client=FakeSqs())
    assert response["statusCode"] == 404


def test_missing_session_id(stack):
    _, closer, _ = stack
    response = closer.handle_close_request({"pathParameters": {}}, sqs_client=FakeSqs())
    assert response["statusCode"] == 400


def test_non_terminal_turn_blocks_close(stack):
    """processing 的 turn 還在飛，凍結會產出殘缺 snapshot。"""
    sessions, closer, _ = stack
    seed_turns(statuses=["completed", "processing"])
    seed_session(sessions)
    response = closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    assert response["statusCode"] == 500
    assert sessions.get_session(ELDER, SESSION)["state"] == sessions.STATE_CLOSING


def test_turn_from_other_session_blocks_close(stack):
    sessions, closer, _ = stack
    seed_turns(session_id="ses_other")
    seed_session(sessions)
    response = closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    assert response["statusCode"] == 500


def test_input_bytes_are_recomputed_from_frozen_turns(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    session = sessions.get_session(ELDER, SESSION)
    assert session["input_bytes"] == sum(len(f"第 {index} 句".encode("utf-8")) for index in range(2))
    assert session["session_snapshot_hash"] == sessions.compute_snapshot_hash(
        TURN_IDS, session["input_bytes"]
    )


def test_sqs_failure_does_not_reopen_session(stack):
    """派送失敗只是延遲，closed 必須維持成立。"""
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    sqs = FakeSqs(error=RuntimeError("network down"))

    response = closer.handle_close_request(elder_event(), sqs_client=sqs)
    assert response["statusCode"] == 200
    session = sessions.get_session(ELDER, SESSION)
    assert session["state"] == sessions.STATE_CLOSED
    assert session["batch_status"] == sessions.BATCH_PENDING
    assert session["session_state_key"] == sessions.STATE_KEY_BATCH_PENDING


# -- sweep --------------------------------------------------------------------


def test_pending_sweep_requeues_undelivered_sessions(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    closer.handle_close_request(elder_event(), sqs_client=FakeSqs(error=RuntimeError("down")))

    sqs = FakeSqs()
    assert closer.sweep_pending_batches(sqs_client=sqs) == 1
    assert sqs.messages[0]["session_id"] == SESSION


def test_idle_sweep_closes_stale_active_sessions(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions, last_activity="2020-01-01T00:00:00.000+08:00")

    sqs = FakeSqs()
    result = closer.run_sweep({"sweep": True}, sqs_client=sqs)
    assert result["idle_closed"] == 1
    session = sessions.get_session(ELDER, SESSION)
    assert session["state"] == sessions.STATE_CLOSED
    assert session["close_reason"] == "idle"


def test_idle_sweep_skips_recent_sessions(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions, last_activity="2099-01-01T00:00:00.000+08:00")
    assert closer.run_sweep({"sweep": True}, sqs_client=FakeSqs())["idle_closed"] == 0


def test_idle_sweep_skips_inflight_sessions(stack, caplog):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions, inflight=1, last_activity="2020-01-01T00:00:00.000+08:00")
    with caplog.at_level("INFO"):
        assert closer.sweep_idle_sessions(now=_now(), sqs_client=FakeSqs()) == 0
    assert "仍有 inflight" in caplog.text


# -- inflight 收斂 -------------------------------------------------------------


def reserve_turn(session_id, *, conversation_id, moment, lease_seconds):
    """在指定時間點 reserve 一個 processing turn（供收斂測試控制租約是否到期）。"""
    from src.shared import turns

    original = turns.REQUEST_LEASE_SECONDS
    turns.REQUEST_LEASE_SECONDS = lease_seconds
    try:
        return turns.reserve(
            ELDER,
            session_id,
            turn={
                "conversation_id": conversation_id,
                "idempotency_key": f"req-{conversation_id}",
                "request_hash": "hash-1",
                "lang": "zh-TW",
                "input_type": "text",
            },
            owner="dead-invocation",
            now=moment,
        )
    finally:
        turns.REQUEST_LEASE_SECONDS = original


def stale_session(sessions, *, conversation_id="cnv_flight", lease_seconds=60):
    """做出一個「已閒置很久、卻還掛著 inflight」的 session。"""
    from datetime import datetime

    moment = datetime(2020, 1, 1, tzinfo=TZ_TAIPEI)
    session_id = sessions.create_session(ELDER, now=moment)["session_id"]
    reserve_turn(
        session_id, conversation_id=conversation_id, moment=moment, lease_seconds=lease_seconds
    )
    return session_id


def test_idle_sweep_converges_turns_whose_lease_expired(stack):
    """Lambda 死在半路的 turn 會永遠掛著 inflight，session 也就永遠關不起來。"""
    sessions, closer, _ = stack
    from src.shared import turns

    session_id = stale_session(sessions)

    assert closer.run_sweep({"sweep": True}, sqs_client=FakeSqs())["idle_closed"] == 1
    turn = turns.get_turn(ELDER, "cnv_flight")
    assert turn["request_status"] == turns.STATUS_FAILED
    assert turn["error_code"] == "REQUEST_ABANDONED"

    session = sessions.get_session(ELDER, session_id)
    assert session["state"] == sessions.STATE_CLOSED
    assert session["inflight_turn_ids"] == []
    # 失敗的 turn 不得進入 frozen snapshot
    assert session["turn_ids"] == []


def test_idle_sweep_keeps_turns_whose_lease_is_alive(stack):
    """租約仍有效代表還有 invocation 在飛，接管會做出第二次副作用。"""
    sessions, closer, _ = stack
    from src.shared import turns

    session_id = stale_session(sessions, lease_seconds=10**9)

    assert closer.sweep_idle_sessions(now=_now(), sqs_client=FakeSqs()) == 0
    assert turns.get_turn(ELDER, "cnv_flight")["request_status"] == turns.STATUS_PROCESSING
    assert sessions.get_session(ELDER, session_id)["inflight_turn_count"] == 1


def test_idle_sweep_repairs_reservations_of_terminal_turns(stack):
    """turn 已終態、名額卻沒還回來時，收斂只要修 reservation，不改 turn。"""
    sessions, closer, _ = stack
    from src.shared import turns

    session_id = stale_session(sessions, lease_seconds=10**9)
    boto3.resource("dynamodb").Table(CONVERSATIONS_TABLE).update_item(
        Key={"elder_id": ELDER, "record_id": "TURN#cnv_flight"},
        UpdateExpression="SET request_status = :completed",
        ExpressionAttributeValues={":completed": turns.STATUS_COMPLETED},
    )

    assert closer.run_sweep({"sweep": True}, sqs_client=FakeSqs())["idle_closed"] == 1
    assert turns.get_turn(ELDER, "cnv_flight")["request_status"] == turns.STATUS_COMPLETED
    assert sessions.get_session(ELDER, session_id)["state"] == sessions.STATE_CLOSED


def test_client_close_does_not_take_over_inflight_turns(stack):
    """接管別人的 turn 不是使用者按「結束對話」該做的事；client 一律回 409 重試。"""
    sessions, closer, _ = stack
    from src.shared import turns

    session_id = stale_session(sessions)

    response = closer.handle_close_request(
        elder_event(session_id=session_id), sqs_client=FakeSqs()
    )
    assert response["statusCode"] == 409
    assert body_of(response)["error"]["code"] == "REQUEST_IN_PROGRESS"
    assert turns.get_turn(ELDER, "cnv_flight")["request_status"] == turns.STATUS_PROCESSING


def test_orphan_reservation_is_kept_for_manual_handling(stack, caplog):
    """inflight ID 讀不到 turn 是資料異常；自行清掉會凍出一份可能少了 turn 的 snapshot。"""
    sessions, closer, _ = stack
    seed_session(sessions, inflight=1, last_activity="2020-01-01T00:00:00.000+08:00")

    with caplog.at_level("ERROR"):
        assert closer.sweep_idle_sessions(now=_now(), sqs_client=FakeSqs()) == 0
    assert "找不到對應 turn" in caplog.text
    assert sessions.get_session(ELDER, SESSION)["inflight_turn_count"] == 1


def test_expired_lease_sweep_requeues(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    hash_value = sessions.get_session(ELDER, SESSION)["session_snapshot_hash"]
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="dead", lease_seconds=-60
    )

    sqs = FakeSqs()
    assert closer.sweep_expired_leases(now=_now(), sqs_client=sqs) == 1
    # sweep 只重投，不改狀態；接管由 consumer 的條件式 claim 決定
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_PROCESSING


def test_expired_lease_sweep_skips_active_lease(stack):
    sessions, closer, _ = stack
    seed_turns()
    seed_session(sessions)
    closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    hash_value = sessions.get_session(ELDER, SESSION)["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="alive")
    assert closer.sweep_expired_leases(now=_now(), sqs_client=FakeSqs()) == 0


def _now():
    from datetime import datetime

    from src.extraction.temporal import TZ_TAIPEI

    return datetime.now(TZ_TAIPEI)


# -- DLQ reconciler -----------------------------------------------------------


def dlq_record(snapshot_hash, message_id="dlq-1"):
    return {
        "messageId": message_id,
        "body": json.dumps(
            {"elder_id": ELDER, "session_id": SESSION, "session_snapshot_hash": snapshot_hash}
        ),
    }


def closed_session(sessions, closer):
    seed_turns()
    seed_session(sessions)
    closer.handle_close_request(elder_event(), sqs_client=FakeSqs())
    return sessions.get_session(ELDER, SESSION)


def test_dlq_converges_to_failed_and_alerts(stack):
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    sns = FakeSns()

    outcome = reconciler.process_record(
        dlq_record(session["session_snapshot_hash"]), sns_client=sns
    )
    assert outcome == reconciler.OUTCOME_CONVERGED

    updated = sessions.get_session(ELDER, SESSION)
    assert updated["batch_status"] == sessions.BATCH_FAILED
    assert updated["session_state_key"] == sessions.STATE_KEY_BATCH_FAILED
    assert updated["batch_error"]["code"] == "BATCH_RETRIES_EXHAUSTED"
    assert "batch_lease_owner" not in updated


def test_dlq_alert_payload_contains_no_transcript(stack, monkeypatch):
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    monkeypatch.setattr(reconciler, "ALERT_TOPIC_ARN", "arn:aws:sns:local:1:alerts")
    sns = FakeSns()

    reconciler.process_record(dlq_record(session["session_snapshot_hash"]), sns_client=sns)
    payload = json.loads(sns.published[0]["Message"])
    assert payload["session_id"] == SESSION
    # 告警不得夾帶對話內容
    assert "第 0 句" not in sns.published[0]["Message"]
    assert set(payload) == {"alert", "elder_id", "session_id", "action"}


def test_dlq_with_stale_hash_does_not_touch_session(stack, caplog):
    """hash 不符代表訊息對應舊 snapshot，改它會誤標一個健康的 session。"""
    sessions, closer, reconciler = stack
    closed_session(sessions, closer)
    with caplog.at_level("WARNING"):
        outcome = reconciler.process_record(dlq_record("stale-hash"), sns_client=FakeSns())
    assert outcome == reconciler.OUTCOME_SNAPSHOT_MISMATCH
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_PENDING
    assert "snapshot 已過期" in caplog.text


def test_dlq_does_not_overwrite_completed(stack):
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    hash_value = session["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="w1")
    sessions.complete_batch(ELDER, SESSION, owner="w1", extractor_version="v1")

    outcome = reconciler.process_record(dlq_record(hash_value), sns_client=FakeSns())
    assert outcome == reconciler.OUTCOME_ALREADY_COMPLETED
    assert sessions.get_session(ELDER, SESSION)["batch_status"] == sessions.BATCH_COMPLETED


def test_dlq_already_failed_is_skipped(stack):
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    hash_value = session["session_snapshot_hash"]
    reconciler.process_record(dlq_record(hash_value), sns_client=FakeSns())
    outcome = reconciler.process_record(dlq_record(hash_value), sns_client=FakeSns())
    assert outcome == reconciler.OUTCOME_ALREADY_FAILED


def test_dlq_unknown_session_and_malformed_are_acked(stack):
    sessions, _, reconciler = stack
    assert reconciler.process_record(dlq_record("x")) == reconciler.OUTCOME_SESSION_NOT_FOUND
    assert (
        reconciler.process_record({"messageId": "m", "body": "not json"})
        == reconciler.OUTCOME_MALFORMED
    )
    assert (
        reconciler.process_record({"messageId": "m", "body": json.dumps({"elder_id": ELDER})})
        == reconciler.OUTCOME_MALFORMED
    )


def test_dlq_handler_always_acks(stack):
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    response = reconciler.handler(
        {
            "Records": [
                dlq_record(session["session_snapshot_hash"], message_id="a"),
                {"messageId": "b", "body": "not json"},
            ]
        },
        None,
    )
    assert [item["outcome"] for item in response["outcomes"]] == [
        reconciler.OUTCOME_CONVERGED,
        reconciler.OUTCOME_MALFORMED,
    ]


def test_failed_batch_can_be_replayed_after_dlq(stack):
    """人工 replay：failed → pending，沿用 frozen state 與既有 manifest。"""
    sessions, closer, reconciler = stack
    session = closed_session(sessions, closer)
    hash_value = session["session_snapshot_hash"]
    reconciler.process_record(dlq_record(hash_value), sns_client=FakeSns())

    replayed = sessions.requeue_failed_batch(ELDER, SESSION)
    assert replayed["batch_status"] == sessions.BATCH_PENDING
    assert replayed["session_snapshot_hash"] == hash_value
