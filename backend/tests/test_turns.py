"""Turn 請求狀態機測試（moto）。

對應 framework Verification：`/chat` reserve 與 close race、inflight 回 409、
lease-expired turn 接管或安全失敗移除 reservation。
"""

from datetime import timedelta
import importlib

import boto3
import pytest
from moto import mock_aws

from src.extraction.temporal import TZ_TAIPEI

TABLE_NAME = "conversations-test"
ELDER = "eld_a1b2c3d4e5f6"
SESSION = "ses_01J8"
OWNER = "req-1"
HASH = "hash-1"


@pytest.fixture
def stack(monkeypatch):
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
                {"AttributeName": "session_state_key", "AttributeType": "S"},
                {"AttributeName": "session_state_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "conversations-by-time",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "conversation_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "sessions-by-state",
                    "KeySchema": [
                        {"AttributeName": "session_state_key", "KeyType": "HASH"},
                        {"AttributeName": "session_state_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db = importlib.reload(importlib.import_module("src.shared.db"))
        sessions = importlib.reload(importlib.import_module("src.shared.sessions"))
        turns = importlib.reload(importlib.import_module("src.shared.turns"))
        yield db, sessions, turns

    for name in ("src.shared.db", "src.shared.sessions", "src.shared.turns"):
        importlib.reload(importlib.import_module(name))


def now():
    from datetime import datetime

    return datetime.now(TZ_TAIPEI)


def reserve(turns, sessions, *, conversation_id="cnv_1", owner=OWNER, session_id=SESSION, **extra):
    return turns.reserve(
        ELDER,
        session_id,
        turn={
            "conversation_id": conversation_id,
            "client_request_id": f"req-{conversation_id}",
            "request_hash": HASH,

            "lang": "zh-TW",
            "input_type": "text",
            **extra,
        },
        owner=owner,
    )


def session_of(sessions, session_id=SESSION):
    return sessions.get_session(ELDER, session_id)


# -- 身分與冪等鍵 --------------------------------------------------------------


def test_conversation_id_is_stable_per_request(stack):
    _, _, turns = stack
    first = turns.conversation_id_for(ELDER, "req-abc")
    assert first == turns.conversation_id_for(ELDER, "req-abc")
    assert first != turns.conversation_id_for(ELDER, "req-xyz")
    assert first != turns.conversation_id_for("eld_ffffffffffff", "req-abc")
    assert first.startswith("cnv_")


def test_same_key_with_different_payload_is_a_conflict(stack):
    _, _, turns = stack
    with pytest.raises(turns.TurnConflictError):
        turns.assert_same_request({"request_hash": HASH}, "other-hash")
    # 相同內容不得誤判為衝突
    turns.assert_same_request({"request_hash": HASH}, HASH)


# -- reserve ------------------------------------------------------------------


def test_reserve_creates_processing_turn_and_holds_a_slot(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserved = reserve(turns, sessions, session_id=session["session_id"])

    assert reserved["request_status"] == turns.STATUS_PROCESSING
    assert reserved["request_lease_owner"] == OWNER
    assert reserved["session_id"] == session["session_id"]
    # GSI 排序鍵要在 reserve 當下就寫好，統計與時間軸才看得到這一輪
    assert reserved["conversation_time_key"].endswith("#cnv_1")

    stored = session_of(sessions, session["session_id"])
    assert stored["inflight_turn_ids"] == ["cnv_1"]
    assert stored["inflight_turn_count"] == 1
    assert stored["turn_ids"] == []


def test_reserved_turn_is_not_counted_as_an_interaction(stack):
    """processing 還沒收斂，不能算成一次互動。"""
    db, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserved = reserve(turns, sessions, session_id=session["session_id"])
    date = reserved["created_at"][:10]

    assert db.list_turn_times(ELDER, date, date) == []


def test_reserve_is_rejected_once_the_session_is_closing(stack):
    """close 先成立時，這一輪不得追加進已凍結的 session。"""
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    sessions.begin_closing(ELDER, session["session_id"], close_reason="client_requested")

    with pytest.raises(turns.TurnReserveRejectedError):
        reserve(turns, sessions, session_id=session["session_id"])


def test_reserve_is_rejected_at_the_inflight_limit(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, conversation_id="cnv_1", session_id=session["session_id"])

    with pytest.raises(turns.TurnReserveRejectedError):
        reserve(turns, sessions, conversation_id="cnv_2", session_id=session["session_id"])


def test_reserve_of_an_existing_turn_reports_in_progress(stack):
    """同一個 client_request_id 併發進來：輸掉的那個要回頭走冪等判定，不是再寫一次。"""
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])

    with pytest.raises(turns.TurnInProgressError):
        reserve(turns, sessions, owner="req-2", session_id=session["session_id"])


# -- commit -------------------------------------------------------------------


def commit(turns, session_id, conversation_id="cnv_1", owner=OWNER):
    return turns.commit(
        ELDER,
        conversation_id,
        session_id=session_id,
        owner=owner,
        result={
            "elder_transcript": "我吃過血壓藥了",
            "ai_respond_text": "有按時吃藥真棒！",
            "routines_updated": True,
        },
    )


def test_commit_finishes_the_turn_and_returns_the_slot(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])
    committed = commit(turns, session["session_id"])

    assert committed["request_status"] == turns.STATUS_COMPLETED
    assert committed["system_status"] == "success"
    assert committed["ai_respond_text"] == "有按時吃藥真棒！"
    assert "request_lease_owner" not in committed

    stored = session_of(sessions, session["session_id"])
    assert stored["turn_ids"] == ["cnv_1"]
    assert stored["turn_count"] == 1
    assert stored["inflight_turn_ids"] == []
    assert stored["inflight_turn_count"] == 0
    assert stored["input_bytes"] == turns.input_bytes_of(
        {"elder_transcript": "我吃過血壓藥了", "ai_respond_text": "有按時吃藥真棒！"}
    )


def test_committed_turn_is_countable_as_an_interaction(stack):
    """寫入端與統計讀取端的接縫：commit 後統計必須看得到這一輪。"""
    db, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserved = reserve(turns, sessions, session_id=session["session_id"])
    commit(turns, session["session_id"])
    date = reserved["created_at"][:10]

    assert db.list_turn_times(ELDER, date, date) == [reserved["created_at"]]


def test_commit_requires_the_current_lease_owner(stack):
    """租約被接管後，原 invocation 遲到的提交不得覆寫結果。"""
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])

    with pytest.raises(turns.TurnTransitionRejectedError):
        commit(turns, session["session_id"], owner="someone-else")
    assert session_of(sessions, session["session_id"])["inflight_turn_count"] == 1


def test_commit_twice_is_rejected(stack):
    """終態不可再被推進；重送要靠冪等判定重播，而不是重跑一次 commit。"""
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])
    commit(turns, session["session_id"])

    with pytest.raises(turns.TurnTransitionRejectedError):
        commit(turns, session["session_id"])
    assert session_of(sessions, session["session_id"])["turn_ids"] == ["cnv_1"]


def test_turns_are_appended_in_acceptance_order(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    for index in (1, 2, 3):
        reserve(turns, sessions, conversation_id=f"cnv_{index}", session_id=session["session_id"])
        commit(turns, session["session_id"], conversation_id=f"cnv_{index}")

    stored = session_of(sessions, session["session_id"])
    assert stored["turn_ids"] == ["cnv_1", "cnv_2", "cnv_3"]
    assert stored["recent_conversation_ids"] == ["cnv_1", "cnv_2", "cnv_3"]


# -- fail 與接管 ---------------------------------------------------------------


def test_fail_is_terminal_and_frees_the_session(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])

    failed = turns.fail(
        ELDER,
        "cnv_1",
        session_id=session["session_id"],
        code="TTS_FAILED",
        message="語音合成失敗",
        http_status=500,
        owner=OWNER,
    )
    assert failed["request_status"] == turns.STATUS_FAILED
    assert failed["error_code"] == "TTS_FAILED"
    assert failed["error_http_status"] == 500
    assert "request_lease_owner" not in failed

    stored = session_of(sessions, session["session_id"])
    assert stored["inflight_turn_ids"] == []
    assert stored["inflight_turn_count"] == 0
    # 失敗的 turn 不進 session 的已提交清單
    assert stored["turn_ids"] == []


def test_failed_turn_is_not_counted_as_an_interaction(stack):
    db, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserved = reserve(turns, sessions, session_id=session["session_id"])
    turns.fail(
        ELDER,
        "cnv_1",
        session_id=session["session_id"],
        code="BEDROCK_ERROR",
        message="呼叫對話大腦失敗",
        http_status=500,
    )
    date = reserved["created_at"][:10]

    assert db.list_turn_times(ELDER, date, date) == []


def test_lease_is_not_takeable_before_it_expires(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserved = reserve(turns, sessions, session_id=session["session_id"])

    assert turns.is_lease_expired(reserved) is False
    with pytest.raises(turns.TurnTransitionRejectedError):
        turns.take_over(ELDER, "cnv_1", owner="req-2")


def test_expired_lease_can_be_taken_over(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])
    later = now() + timedelta(seconds=turns.REQUEST_LEASE_SECONDS + 1)

    taken = turns.take_over(ELDER, "cnv_1", owner="req-2", now=later)
    assert taken["request_lease_owner"] == "req-2"
    assert taken["request_status"] == turns.STATUS_PROCESSING
    # 接管不重新 reserve：原本的名額仍然是同一個
    assert session_of(sessions, session["session_id"])["inflight_turn_ids"] == ["cnv_1"]

    commit(turns, session["session_id"], owner="req-2")
    assert session_of(sessions, session["session_id"])["turn_ids"] == ["cnv_1"]


def test_completed_turn_cannot_be_taken_over(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])
    commit(turns, session["session_id"])
    later = now() + timedelta(days=1)

    with pytest.raises(turns.TurnTransitionRejectedError):
        turns.take_over(ELDER, "cnv_1", owner="req-2", now=later)


# -- reservation 修復 ----------------------------------------------------------


def test_release_reservation_is_idempotent(stack):
    _, sessions, turns = stack
    session = sessions.create_session(ELDER)
    reserve(turns, sessions, session_id=session["session_id"])

    assert turns.release_reservation(ELDER, session["session_id"], "cnv_1") is True
    assert turns.release_reservation(ELDER, session["session_id"], "cnv_1") is False
    assert session_of(sessions, session["session_id"])["inflight_turn_count"] == 0
