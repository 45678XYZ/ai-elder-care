"""摘要 `data_status` 依賴的 session 待處理判斷。

規則見 docs/framework.md：active／closing 還會長出新 turn；closed 但 batch 未完成表示
一般事件尚未 materialize。缺值與未知狀態一律保守視為未完成。
"""

from unittest.mock import patch

import pytest

from src.shared import sessions

ELDER = "eld_a1b2c3d4e5f6"


def make_session(session_id: str, state: str, batch_status: str | None = None):
    session = {"session_id": session_id, "elder_id": ELDER, "state": state}
    if batch_status is not None:
        session["batch_status"] = batch_status
    return session


@pytest.mark.parametrize(
    "session,expected",
    [
        (make_session("ses_1", sessions.STATE_ACTIVE), True),
        (make_session("ses_2", sessions.STATE_CLOSING), True),
        (make_session("ses_3", sessions.STATE_CLOSED, sessions.BATCH_PENDING), True),
        (make_session("ses_4", sessions.STATE_CLOSED, sessions.BATCH_PROCESSING), True),
        (make_session("ses_5", sessions.STATE_CLOSED, sessions.BATCH_FAILED), True),
        (make_session("ses_6", sessions.STATE_CLOSED, sessions.BATCH_COMPLETED), False),
        # closed 但沒有 batch_status：資料不完整，不得宣稱已完成
        (make_session("ses_7", sessions.STATE_CLOSED), True),
        (make_session("ses_8", "unknown"), True),
    ],
)
def test_is_pending_materialization(session, expected):
    assert sessions.is_pending_materialization(session) is expected


def test_list_pending_sessions_reads_each_candidate_consistently():
    stored = {
        "ses_a": make_session("ses_a", sessions.STATE_CLOSED, sessions.BATCH_COMPLETED),
        "ses_b": make_session("ses_b", sessions.STATE_CLOSED, sessions.BATCH_PENDING),
        "ses_c": make_session("ses_c", sessions.STATE_ACTIVE),
    }
    with patch.object(sessions, "get_session", side_effect=lambda _, sid: stored.get(sid)) as mock:
        pending = sessions.list_pending_sessions(ELDER, ["ses_a", "ses_b", "ses_b", "ses_c"])

    assert [item["session_id"] for item in pending] == ["ses_b", "ses_c"]
    # 重複的候選只讀一次
    assert mock.call_count == 3


def test_list_pending_sessions_counts_missing_session():
    with patch.object(sessions, "get_session", return_value=None):
        pending = sessions.list_pending_sessions(ELDER, ["ses_missing"])
    assert [item["session_id"] for item in pending] == ["ses_missing"]
