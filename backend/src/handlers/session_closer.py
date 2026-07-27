"""Session closer：明確關閉端點與週期性收斂。

兩個入口共用同一段狀態轉換（規範見 docs/api.md 的 close endpoint 與 docs/framework.md 的
「Session close、SQS recovery 與 DLQ」）：

- `POST /chat/sessions/{session_id}/close`：只有 token 對應的長者本人可呼叫；session 不存在
  或不屬於該長者一律回 404 `SESSION_NOT_FOUND`，不用 403 區分，避免洩漏 session 是否存在。
- EventBridge 週期掃描：idle `ACTIVE` session 收斂、`BATCH#PENDING` 補投（closed 之後
  SendMessage 中斷的情況）、`BATCH#PROCESSING` lease 過期重投。

close 的冪等性靠 session 狀態本身，不需要 `client_request_id`：inflight 未清空回 409、
已 closed 回相同 `closed_at`。SQS 傳送失敗不會讓 session reopen，補投由 sweep 負責。
"""

from typing import Any
import json
import logging
import os

import boto3

from src.extraction.temporal import TZ_TAIPEI, format_ts, parse_ts
from src.shared import auth, db, metrics, responses, sessions

logger = logging.getLogger(__name__)

BATCH_QUEUE_URL = os.environ.get("BATCH_QUEUE_URL", "")

# 超過這段時間沒有互動的 active session 由週期性 closer 收斂
SESSION_IDLE_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "30"))

# 每次 sweep 處理的 session 數上限，避免單次執行超時
SWEEP_LIMIT = int(os.environ.get("SESSION_SWEEP_LIMIT", "25"))

_sqs_client = None


def get_sqs_client():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def handler(event, context):
    """依事件來源分派：API Gateway 請求或 EventBridge 排程。"""
    if event.get("source") == "aws.events" or event.get("sweep"):
        return run_sweep(event)
    return handle_close_request(event)


# -----------------------------------------------------------------------------
# API：明確關閉
# -----------------------------------------------------------------------------


def handle_close_request(event, *, sqs_client=None) -> dict[str, Any]:
    session_id = (event.get("pathParameters") or {}).get("session_id") or ""
    if not session_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 session_id")

    try:
        caller = auth.get_caller(event)
    except auth.AuthError as exc:
        return exc.response

    # 只有長者本人可呼叫；照護者沒有 elder_id claim
    if caller.role != auth.ROLE_ELDER or not caller.elder_id:
        return _session_not_found()

    session = sessions.get_session(caller.elder_id, session_id)
    if session is None:
        return _session_not_found()

    try:
        closed = close_session(caller.elder_id, session_id, close_reason="client_requested")
    except sessions.SessionInflightError:
        # inflight 未收斂：App 重試同一個 close call，不產生新的 ID
        return responses.error(409, "REQUEST_IN_PROGRESS", "session 仍有處理中的對話")
    except sessions.SessionNotFoundError:
        return _session_not_found()
    except sessions.SessionError as exc:
        logger.exception("close session 失敗：session_id=%s", session_id)
        return responses.error(500, "INTERNAL_ERROR", str(exc))

    enqueue_batch(closed, client=sqs_client)
    return responses.json_response(
        200,
        {
            "session_id": session_id,
            "status": "closed",
            "closed_at": closed.get("closed_at"),
            "batch_status": closed.get("batch_status"),
        },
    )


def _session_not_found() -> dict[str, Any]:
    return responses.error(404, "SESSION_NOT_FOUND", "找不到指定的 session")


def close_session(elder_id: str, session_id: str, *, close_reason: str) -> dict[str, Any]:
    """`active/closing → closed`：凍結、驗證、排入 batch。

    驗證這一步不能省：frozen `turn_ids` 若讀不回對應的 turn，代表 snapshot 與實際資料不一致，
    此時 closed 會凍結一份殘缺輸入，batch 之後永遠補不回來。
    """
    session = sessions.begin_closing(elder_id, session_id, close_reason=close_reason)
    if session.get("state") == sessions.STATE_CLOSED:
        return session

    turn_ids = list(session.get("turn_ids") or [])
    frozen_turns = sessions.get_frozen_turns(elder_id, turn_ids)
    input_bytes = _verify_snapshot(session, frozen_turns)

    return sessions.finalize_closed(
        elder_id, session_id, turn_ids=turn_ids, input_bytes=input_bytes
    )


def _verify_snapshot(session: dict[str, Any], frozen_turns: list[dict[str, Any]]) -> int:
    """檢查 frozen turns 都是 terminal 且屬於本 session，並重算 input bytes。"""
    session_id = session.get("session_id")
    total_bytes = 0
    for turn in frozen_turns:
        if turn.get("session_id") not in (None, session_id):
            raise sessions.SessionError(
                f"frozen turn 不屬於本 session：turn={turn.get('conversation_id')}"
            )
        status = turn.get("request_status")
        if status is not None and status != "completed":
            # 只有 completed 的 turn 能進 snapshot；processing 代表還在飛
            raise sessions.SessionError(
                f"frozen turn 非 terminal：turn={turn.get('conversation_id')} status={status}"
            )
        for field in ("ai_prompt_text", "elder_transcript", "ai_respond_text"):
            total_bytes += len((turn.get(field) or "").encode("utf-8"))
    return total_bytes


def enqueue_batch(session: dict[str, Any], *, client=None) -> bool:
    """把 closed session 投進 batch 佇列。

    刻意不與 DynamoDB 寫入放在同一個交易：closed 必須先成立，SendMessage 失敗只是延遲，
    `BATCH#PENDING` sweep 會補投。回傳是否成功送出，供觀測。
    """
    if not BATCH_QUEUE_URL:
        logger.warning("未設定 BATCH_QUEUE_URL，略過派送：session_id=%s", session.get("session_id"))
        return False
    if session.get("batch_status") != sessions.BATCH_PENDING:
        # 已在處理或已完成的 session 不需要再投
        return False

    payload = {
        "elder_id": session["elder_id"],
        "session_id": session["session_id"],
        "session_snapshot_hash": session["session_snapshot_hash"],
    }
    try:
        (client or get_sqs_client()).send_message(
            QueueUrl=BATCH_QUEUE_URL, MessageBody=json.dumps(payload, ensure_ascii=False)
        )
        return True
    except Exception:
        # 派送失敗不影響 closed；留給 recovery sweep
        logger.exception("派送 batch 訊息失敗，將由 sweep 補投：session_id=%s", session["session_id"])
        return False


# -----------------------------------------------------------------------------
# EventBridge：週期性收斂
# -----------------------------------------------------------------------------


def run_sweep(event: dict[str, Any] | None = None, *, sqs_client=None) -> dict[str, Any]:
    """三種 sweep 一次跑完，回報各自處理的數量供觀測。"""
    now = parse_ts(format_ts(_now()))
    results = {
        "idle_closed": sweep_idle_sessions(now=now, sqs_client=sqs_client),
        "pending_requeued": sweep_pending_batches(sqs_client=sqs_client),
        "processing_requeued": sweep_expired_leases(now=now, sqs_client=sqs_client),
    }
    metrics.emit_sweep_result(results)
    return results


def _now():
    from datetime import datetime

    return datetime.now(TZ_TAIPEI)


def sweep_idle_sessions(*, now, sqs_client=None) -> int:
    """收斂閒置的 active session。

    未明確 close 的 session 若不收斂，其一般事件永遠不會 materialize，照護者端就會少資料。
    """
    from datetime import timedelta

    cutoff = format_ts(now - timedelta(minutes=SESSION_IDLE_MINUTES))
    candidates = sessions.list_sessions_by_state(
        sessions.STATE_KEY_ACTIVE, before=cutoff, limit=SWEEP_LIMIT
    )

    closed_count = 0
    for candidate in candidates:
        elder_id, session_id = candidate["elder_id"], candidate["session_id"]
        try:
            closed = close_session(elder_id, session_id, close_reason="idle")
        except sessions.SessionInflightError:
            # 仍有 inflight：這一輪跳過，下一輪 lease 過期後再試
            logger.info("session 仍有 inflight，稍後再收斂：session_id=%s", session_id)
            continue
        except sessions.SessionError:
            logger.exception("idle close 失敗：session_id=%s", session_id)
            continue
        enqueue_batch(closed, client=sqs_client)
        closed_count += 1
    return closed_count


def sweep_pending_batches(*, sqs_client=None) -> int:
    """補投 `BATCH#PENDING`：closed 之後 SendMessage 中斷的 session 靠這裡救回。"""
    candidates = sessions.list_sessions_by_state(
        sessions.STATE_KEY_BATCH_PENDING, limit=SWEEP_LIMIT
    )
    return sum(1 for candidate in candidates if enqueue_batch(candidate, client=sqs_client))


def sweep_expired_leases(*, now, sqs_client=None) -> int:
    """重投 lease 已過期的 `BATCH#PROCESSING`。

    只重投、不改狀態：實際的接管由 consumer 的條件式 claim 決定，這樣即使 sweep 與
    原 worker 同時動作也不會有兩個 owner。
    """
    candidates = sessions.list_sessions_by_state(
        sessions.STATE_KEY_BATCH_PROCESSING, limit=SWEEP_LIMIT
    )
    requeued = 0
    for candidate in candidates:
        if not sessions.is_lease_expired(candidate, now=now):
            continue
        payload = dict(candidate)
        payload["batch_status"] = sessions.BATCH_PENDING
        if enqueue_batch(payload, client=sqs_client):
            requeued += 1
    return requeued
