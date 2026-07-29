"""POST /chat/sessions/{session_id}/close — Session 關閉與週期性收斂。規格見 docs/api.md 與 docs/framework.md。

處理途徑與流程：
1. POST /chat/sessions/{session_id}/close（API 入口）：
   - 權限驗證：限長者本人呼叫（長者 mode，帶 elder_id claim）
   - 安全遮蔽：不存在或非本人存取一律回 404 SESSION_NOT_FOUND，不以 403 暴露 session 存在性
   - 狀態轉移：執行 active/closing → closed 條件式轉移與 snapshot 凍結驗證（若有 inflight turn 則回 409）
   - 非同步派送：將 closed session 派送至 SQS batch queue

2. EventBridge 排程掃描（run_sweep 入口）：
   - 閒置收斂 (idle)：收斂超過 SESSION_IDLE_MINUTES 未關閉的 active session，避免未關閉 session 導致一般事件無法離線 materialize
   - 漏投補發 (pending)：補投已 closed 但因網路/SQS 暫時異常未成功 SendMessage 的 BATCH#PENDING 訊息
   - Lease 過期重投 (processing)：重新派送租約過期的 BATCH#PROCESSING 訊息，交由 consumer 條件式接管
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

# 超過此門檻時間（分鐘）無互動的 active session 將由週期性掃描自動收斂
SESSION_IDLE_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "30"))

# 單次 sweep 掃描與處理的 session 數量上限，避免 Lambda 執行超時
SWEEP_LIMIT = int(os.environ.get("SESSION_SWEEP_LIMIT", "25"))

_sqs_client = None


def get_sqs_client():
    """取得或初始化 SQS boto3 client（Singleton 模式，避免重複建立連線）。"""
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def handler(event, context):
    """Session closer Lambda 入口；依事件來源分派至 API 請求或 EventBridge 排程掃描。"""
    if event.get("source") == "aws.events" or event.get("sweep"):
        return run_sweep(event)
    return handle_close_request(event)


# -----------------------------------------------------------------------------
# API：明確關閉
# -----------------------------------------------------------------------------


def handle_close_request(event, *, sqs_client=None) -> dict[str, Any]:
    """處理 POST /chat/sessions/{session_id}/close 明確關閉請求。"""
    session_id = (event.get("pathParameters") or {}).get("session_id") or ""
    if not session_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 session_id")

    try:
        caller = auth.get_caller(event)
    except auth.AuthError as exc:
        return exc.response

    # 僅長者本人 Token 可關閉自己的 session；照護者 Token 不具 elder_id claim，隱蔽回傳 404
    if caller.role != auth.ROLE_ELDER or not caller.elder_id:
        return _session_not_found()

    session = sessions.get_session(caller.elder_id, session_id)
    if session is None:
        return _session_not_found()

    try:
        closed = close_session(caller.elder_id, session_id, close_reason="client_requested")
    except sessions.SessionInflightError:
        # 仍有飛途中（processing）對話 turn：回傳 409 提示用戶端稍後重試，維護 snapshot 完整性
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
    """回傳標準 404 SESSION_NOT_FOUND 回應（隱蔽內部權限細節）。"""
    return responses.error(404, "SESSION_NOT_FOUND", "找不到指定的 session")


def close_session(elder_id: str, session_id: str, *, close_reason: str) -> dict[str, Any]:
    """執行 active/closing → closed 狀態轉換，包含快照驗證與凍結。

    驗證機制（_verify_snapshot）：若凍結之 turn_ids 無法完整讀回對應 turn，
    代表 snapshot 與實際資料不一致，此時若強制 closed 會凍結殘缺輸入，離線 batch 將永遠無法補齊。
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
    """檢查凍結的 turns 均屬於本 session 且已到達 completed 終態，並計算總輸入 bytes 數。"""
    session_id = session.get("session_id")
    total_bytes = 0
    for turn in frozen_turns:
        if turn.get("session_id") not in (None, session_id):
            raise sessions.SessionError(
                f"frozen turn 不屬於本 session：turn={turn.get('conversation_id')}"
            )
        status = turn.get("request_status")
        if status is not None and status != "completed":
            # 只有 completed 狀態的 turn 允許進入 snapshot；processing 狀態表示仍有運算中對話
            raise sessions.SessionError(
                f"frozen turn 非 terminal：turn={turn.get('conversation_id')} status={status}"
            )
        for field in ("ai_prompt_text", "elder_transcript", "ai_respond_text"):
            total_bytes += len((turn.get(field) or "").encode("utf-8"))
    return total_bytes


def enqueue_batch(session: dict[str, Any], *, client=None) -> bool:
    """將 closed session 派送至 SQS batch queue。

    派送與 DB 狀態寫入解耦：DB closed 必須優先成立，SendMessage 失敗僅造成延遲，
    後續由 EventBridge sweep 補投以確保最終一致性。
    """
    if not BATCH_QUEUE_URL:
        logger.warning("未設定 BATCH_QUEUE_URL，略過派送：session_id=%s", session.get("session_id"))
        return False
    if session.get("batch_status") != sessions.BATCH_PENDING:
        # 已處於 processing 或 completed 狀態的 session 無需重複派送
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
        # 派送失敗不破壞已成立的 closed 狀態；交由 recovery sweep 自動補投
        logger.exception("派送 batch 訊息失敗，將由 sweep 補投：session_id=%s", session["session_id"])
        return False


# -----------------------------------------------------------------------------
# EventBridge：週期性收斂
# -----------------------------------------------------------------------------


def run_sweep(event: dict[str, Any] | None = None, *, sqs_client=None) -> dict[str, Any]:
    """執行 EventBridge 週期性掃描，依序完成閒置收斂、pending 補投與過期 lease 重投。"""
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
    """收斂超過閒置時間門檻的 active session。

    未明確 close 的 session 若不自動收斂，其離線 batch extraction 永遠不會觸發，
    將導致照護者介面缺少一般生活事件資料。
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
            # 仍有飛途中 turn：本輪跳過，待下一輪租約過期或處理完成後再次收斂
            logger.info("session 仍有 inflight，稍後再收斂：session_id=%s", session_id)
            continue
        except sessions.SessionError:
            logger.exception("idle close 失敗：session_id=%s", session_id)
            continue
        enqueue_batch(closed, client=sqs_client)
        closed_count += 1
    return closed_count


def sweep_pending_batches(*, sqs_client=None) -> int:
    """補投 BATCH#PENDING 訊息。修復 DB closed 成功但先前 SQS SendMessage 失敗的極端邊界。"""
    candidates = sessions.list_sessions_by_state(
        sessions.STATE_KEY_BATCH_PENDING, limit=SWEEP_LIMIT
    )
    return sum(1 for candidate in candidates if enqueue_batch(candidate, client=sqs_client))


def sweep_expired_leases(*, now, sqs_client=None) -> int:
    """重投處理租約已過期的 BATCH#PROCESSING 訊息。

    僅重新派送訊息至佇列而不修改 DB 狀態；實際租約接管由 consumer 在執行時發起條件式 claim 決定，
    避免 sweep 與原 worker 並行時產生雙重 owner 競態。
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

