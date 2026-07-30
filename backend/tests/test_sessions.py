"""Session 生命週期與 batch 狀態機測試（moto）。

對應 framework Verification：close immutable／inflight recovery、manifest retry reuse、
SQS duplicate／DLQ／recovery 的 claim 規則。
"""

import importlib

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "conversations-test"
ELDER = "eld_a1b2c3d4e5f6"
SESSION = "ses_01J8"
TURN_IDS = ["cnv_001", "cnv_002", "cnv_003"]
INPUT_BYTES = 512


@pytest.fixture
def sessions(monkeypatch):
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
        importlib.reload(importlib.import_module("src.shared.db"))
        module = importlib.reload(importlib.import_module("src.shared.sessions"))
        yield module

    importlib.reload(importlib.import_module("src.shared.db"))
    importlib.reload(importlib.import_module("src.shared.sessions"))


def seed_active_session(sessions, *, inflight=0, turn_ids=None):
    return sessions.put_session(
        {
            "elder_id": ELDER,
            "session_id": SESSION,
            "state": sessions.STATE_ACTIVE,
            "started_at": "2026-07-26T09:00:00.000+08:00",
            "last_activity_at": "2026-07-26T09:20:00.000+08:00",
            "turn_ids": list(turn_ids or TURN_IDS),
            "turn_count": len(turn_ids or TURN_IDS),
            "inflight_turn_ids": [f"cnv_inflight_{index}" for index in range(inflight)],
            "inflight_turn_count": inflight,
            "input_bytes": INPUT_BYTES,
            "session_state_key": sessions.STATE_KEY_ACTIVE,
            "session_state_time_key": f"2026-07-26T09:20:00.000+08:00#{ELDER}#{SESSION}",
        }
    )


def close_fully(sessions):
    seed_active_session(sessions)
    sessions.begin_closing(ELDER, SESSION, close_reason="client_requested")
    return sessions.finalize_closed(
        ELDER, SESSION, turn_ids=TURN_IDS, input_bytes=INPUT_BYTES
    )


# -- snapshot hash ------------------------------------------------------------


def test_snapshot_hash_depends_on_order_and_bytes(sessions):
    base = sessions.compute_snapshot_hash(TURN_IDS, INPUT_BYTES)
    assert base == sessions.compute_snapshot_hash(TURN_IDS, INPUT_BYTES)
    # turn 順序是輸入的一部分
    assert base != sessions.compute_snapshot_hash(list(reversed(TURN_IDS)), INPUT_BYTES)
    assert base != sessions.compute_snapshot_hash(TURN_IDS, INPUT_BYTES + 1)


# -- close 流程 ---------------------------------------------------------------


def test_close_flow_sets_closed_and_queues_batch(sessions):
    closed = close_fully(sessions)
    assert closed["state"] == sessions.STATE_CLOSED
    assert closed["batch_status"] == sessions.BATCH_PENDING
    assert closed["session_state_key"] == sessions.STATE_KEY_BATCH_PENDING
    assert closed["closed_at"]
    assert closed["session_snapshot_hash"] == sessions.compute_snapshot_hash(TURN_IDS, INPUT_BYTES)
    assert closed["turn_count"] == len(TURN_IDS)


def test_close_is_rejected_while_inflight(sessions):
    seed_active_session(sessions, inflight=1)
    with pytest.raises(sessions.SessionInflightError):
        sessions.begin_closing(ELDER, SESSION, close_reason="client_requested")


def test_close_of_unknown_session_raises(sessions):
    with pytest.raises(sessions.SessionNotFoundError):
        sessions.begin_closing(ELDER, "ses_nope", close_reason="client_requested")


def test_close_is_idempotent(sessions):
    close_fully(sessions)
    # 已 closed 再次呼叫視為冪等，不拋錯也不改狀態
    sessions.begin_closing(ELDER, SESSION, close_reason="client_requested")
    again = sessions.finalize_closed(ELDER, SESSION, turn_ids=TURN_IDS, input_bytes=INPUT_BYTES)
    assert again["state"] == sessions.STATE_CLOSED


def test_closed_session_keeps_frozen_turn_ids(sessions):
    closed = close_fully(sessions)
    assert closed["turn_ids"] == TURN_IDS
    # closed 後不再改 frozen 欄位；重複 finalize 不應改變 hash
    same = sessions.finalize_closed(ELDER, SESSION, turn_ids=TURN_IDS, input_bytes=INPUT_BYTES)
    assert same["session_snapshot_hash"] == closed["session_snapshot_hash"]


# -- chunk manifest -----------------------------------------------------------


def test_manifest_is_persisted_once_and_reused(sessions):
    close_fully(sessions)
    first = [{"chunk_id": "chk_a", "ordinal": 0, "core_start": 0, "core_end": 2,
              "context_start": 0, "context_end": 2,
              "first_core_turn_id": "cnv_001", "last_core_turn_id": "cnv_003"}]
    second = [{"chunk_id": "chk_b", "ordinal": 0, "core_start": 0, "core_end": 2,
               "context_start": 0, "context_end": 2,
               "first_core_turn_id": "cnv_001", "last_core_turn_id": "cnv_003"}]

    stored = sessions.persist_chunk_manifest(ELDER, SESSION, first, planner_version="v1")
    assert stored[0]["chunk_id"] == "chk_a"

    # 第二次寫入不得覆蓋；retry／duplicate／DLQ replay 必須拿到同一份
    reused = sessions.persist_chunk_manifest(ELDER, SESSION, second, planner_version="v2")
    assert reused[0]["chunk_id"] == "chk_a"
    assert sessions.get_session(ELDER, SESSION)["chunk_planner_version"] == "v1"


# -- batch claim --------------------------------------------------------------


def test_claim_acquires_pending_batch(sessions):
    closed = close_fully(sessions)
    outcome, session = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    assert outcome == sessions.CLAIM_ACQUIRED
    assert session["batch_status"] == sessions.BATCH_PROCESSING
    assert session["batch_lease_owner"] == "worker-1"
    assert session["batch_attempts"] == 1
    assert session["session_state_key"] == sessions.STATE_KEY_BATCH_PROCESSING


def test_duplicate_delivery_with_active_lease_is_not_claimed(sessions):
    closed = close_fully(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="worker-1")

    outcome, _ = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="worker-2"
    )
    assert outcome == sessions.CLAIM_LEASE_ACTIVE
    # 原 owner 未被搶走
    assert sessions.get_session(ELDER, SESSION)["batch_lease_owner"] == "worker-1"


def test_expired_lease_can_be_taken_over(sessions):
    closed = close_fully(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="worker-1", lease_seconds=-10
    )

    outcome, session = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="worker-2"
    )
    assert outcome == sessions.CLAIM_ACQUIRED
    assert session["batch_lease_owner"] == "worker-2"
    assert session["batch_attempts"] == 2


def test_completed_batch_cannot_be_claimed(sessions):
    closed = close_fully(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="worker-1")
    sessions.complete_batch(ELDER, SESSION, owner="worker-1", extractor_version="v1")

    outcome, _ = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="worker-2"
    )
    assert outcome == sessions.CLAIM_ALREADY_COMPLETED


def test_failed_batch_cannot_be_claimed_by_workers(sessions):
    """failed 只給人工 replay，自動流程不得搶走。"""
    closed = close_fully(sessions)
    hash_value = closed["session_snapshot_hash"]
    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="worker-1")
    sessions.fail_batch(ELDER, SESSION, owner="worker-1", code="X", message="boom")

    outcome, _ = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=hash_value, owner="worker-2"
    )
    assert outcome == sessions.CLAIM_ALREADY_FAILED


def test_snapshot_mismatch_is_detected(sessions):
    close_fully(sessions)
    outcome, _ = sessions.claim_batch(
        ELDER, SESSION, snapshot_hash="stale-hash", owner="worker-1"
    )
    assert outcome == sessions.CLAIM_SNAPSHOT_MISMATCH


def test_claim_of_unknown_session(sessions):
    outcome, _ = sessions.claim_batch(
        ELDER, "ses_nope", snapshot_hash="x", owner="worker-1"
    )
    assert outcome == sessions.CLAIM_NOT_FOUND


def test_claim_requires_closed_state(sessions):
    seed_active_session(sessions)
    outcome, _ = sessions.claim_batch(ELDER, SESSION, snapshot_hash="x", owner="worker-1")
    assert outcome == sessions.CLAIM_NOT_CLOSED


# -- complete / fail / release ------------------------------------------------


def test_complete_clears_gsi_keys_and_lease(sessions):
    closed = close_fully(sessions)
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    completed = sessions.complete_batch(
        ELDER, SESSION, owner="worker-1", extractor_version="batch-extractor-1"
    )
    assert completed["batch_status"] == sessions.BATCH_COMPLETED
    assert "session_state_key" not in completed
    assert "batch_lease_owner" not in completed
    assert completed["batch_extractor_version"] == "batch-extractor-1"


def test_complete_by_wrong_owner_is_rejected(sessions):
    """lease 被接管後，原 owner 遲到的完成不可覆寫。"""
    closed = close_fully(sessions)
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    with pytest.raises(sessions.SessionError):
        sessions.complete_batch(ELDER, SESSION, owner="worker-2", extractor_version="v1")


def test_fail_sets_failed_state_and_error(sessions):
    closed = close_fully(sessions)
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    failed = sessions.fail_batch(
        ELDER, SESSION, owner="worker-1", code="EVENT_CONFLICT", message="內容互斥"
    )
    assert failed["batch_status"] == sessions.BATCH_FAILED
    assert failed["session_state_key"] == sessions.STATE_KEY_BATCH_FAILED
    assert failed["batch_error"]["code"] == "EVENT_CONFLICT"
    assert "batch_lease_owner" not in failed


def test_fail_cannot_overwrite_completed(sessions):
    closed = close_fully(sessions)
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    sessions.complete_batch(ELDER, SESSION, owner="worker-1", extractor_version="v1")
    with pytest.raises(sessions.SessionError):
        sessions.fail_batch(ELDER, SESSION, owner=None, code="X", message="late failure")


def test_release_lease_returns_to_pending(sessions):
    closed = close_fully(sessions)
    sessions.claim_batch(
        ELDER, SESSION, snapshot_hash=closed["session_snapshot_hash"], owner="worker-1"
    )
    sessions.release_batch_lease(ELDER, SESSION, owner="worker-1")
    session = sessions.get_session(ELDER, SESSION)
    assert session["batch_status"] == sessions.BATCH_PENDING
    assert session["session_state_key"] == sessions.STATE_KEY_BATCH_PENDING
    assert "batch_lease_owner" not in session


def test_requeue_failed_batch_only_from_failed(sessions):
    closed = close_fully(sessions)
    hash_value = closed["session_snapshot_hash"]
    with pytest.raises(sessions.SessionError):
        sessions.requeue_failed_batch(ELDER, SESSION)

    sessions.claim_batch(ELDER, SESSION, snapshot_hash=hash_value, owner="worker-1")
    sessions.fail_batch(ELDER, SESSION, owner="worker-1", code="X", message="boom")
    replayed = sessions.requeue_failed_batch(ELDER, SESSION)
    assert replayed["batch_status"] == sessions.BATCH_PENDING
    # replay 沿用 frozen state 與既有 manifest
    assert replayed["session_snapshot_hash"] == hash_value


# -- 狀態索引與 frozen turns ---------------------------------------------------


def test_list_sessions_by_state(sessions):
    close_fully(sessions)
    pending = sessions.list_sessions_by_state(sessions.STATE_KEY_BATCH_PENDING)
    assert [item["session_id"] for item in pending] == [SESSION]
    assert sessions.list_sessions_by_state(sessions.STATE_KEY_BATCH_FAILED) == []


def test_list_sessions_by_state_respects_before_cursor(sessions):
    close_fully(sessions)
    assert sessions.list_sessions_by_state(
        sessions.STATE_KEY_BATCH_PENDING, before="2020-01-01T00:00:00.000+08:00"
    ) == []


def test_frozen_turns_are_returned_in_snapshot_order(sessions):
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    for index, turn_id in enumerate(TURN_IDS):
        table.put_item(
            Item={
                "elder_id": ELDER,
                "record_id": f"TURN#{turn_id}",
                "item_type": "conversation",
                "conversation_id": turn_id,
                "created_at": f"2026-07-26T09:{index:02d}:00.000+08:00",
                "elder_transcript": f"第 {index} 句",
            }
        )

    ordered = sessions.get_frozen_turns(ELDER, list(reversed(TURN_IDS)))
    assert [item["conversation_id"] for item in ordered] == list(reversed(TURN_IDS))
    assert sessions.get_frozen_turns(ELDER, []) == []


def test_missing_frozen_turn_raises(sessions):
    with pytest.raises(sessions.SessionError, match="缺少 turn"):
        sessions.get_frozen_turns(ELDER, ["cnv_missing"])


def test_mark_turns_batch_completed(sessions):
    updated = sessions.mark_turns_batch_completed(
        ELDER, {"cnv_001": "chk_a", "cnv_missing": "chk_a"}, extractor_version="v1"
    )
    assert updated == 2



def test_is_lease_expired(sessions):
    assert sessions.is_lease_expired({}) is True
    assert sessions.is_lease_expired({"batch_lease_until": "2020-01-01T00:00:00.000+08:00"}) is True
    assert sessions.is_lease_expired({"batch_lease_until": "2099-01-01T00:00:00.000+08:00"}) is False
