"""DLQ reconciler：把重試耗盡的 batch 訊息收斂成 `failed` 並告警。

規範見 docs/framework.md 第 10、11 點。這支 handler 的重點在「什麼情況下不能動 session」：

- **snapshot hash 不符**：訊息對應的是舊 snapshot，session 早已往前走。改它會把一個健康的
  session 誤標為 failed，所以只記錄並 ack。
- **已 `completed`**：遲到的失敗訊息不得覆寫已完成的結果。
- **已 `failed`**：已經是待人工處理的終態，重複收斂沒有意義。

只有「hash 相符且尚未 terminal」才條件式標記 `failed`、清 lease、寫入安全化後的錯誤，
並發出告警。任何情況都要 ack，否則訊息會在 DLQ 裡無限循環。
"""

from typing import Any
import json
import logging
import os

import boto3

from src.shared import sessions

logger = logging.getLogger(__name__)

ALERT_TOPIC_ARN = os.environ.get("BATCH_ALERT_TOPIC_ARN", "")

OUTCOME_CONVERGED = "converged"
OUTCOME_SNAPSHOT_MISMATCH = "snapshot_mismatch"
OUTCOME_ALREADY_COMPLETED = "already_completed"
OUTCOME_ALREADY_FAILED = "already_failed"
OUTCOME_SESSION_NOT_FOUND = "session_not_found"
OUTCOME_MALFORMED = "malformed"

_sns_client = None


def get_sns_client():
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns")
    return _sns_client


def handler(event, context):
    """DLQ event source 入口；一律 ack，回報各訊息的處置結果供觀測。"""
    outcomes: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId", "")
        try:
            outcome = process_record(record)
        except Exception:
            # 連收斂都失敗時仍然 ack：訊息留在 DLQ 只會反覆炸同一個錯
            logger.exception("DLQ 收斂失敗：message_id=%s", message_id)
            outcome = "error"
        outcomes.append({"message_id": message_id, "outcome": outcome})
    return {"outcomes": outcomes}


def process_record(record: dict[str, Any], *, sns_client=None) -> str:
    try:
        body = json.loads(record.get("body") or "{}")
    except json.JSONDecodeError:
        logger.error("DLQ 訊息不是合法 JSON，無法對應 session")
        return OUTCOME_MALFORMED

    elder_id = body.get("elder_id")
    session_id = body.get("session_id")
    snapshot_hash = body.get("session_snapshot_hash")
    if not (elder_id and session_id and snapshot_hash):
        logger.error("DLQ 訊息缺少 session 對應資訊，無法收斂")
        return OUTCOME_MALFORMED

    session = sessions.get_session(elder_id, session_id)
    if session is None:
        logger.error("DLQ 訊息對應的 session 不存在：session_id=%s", session_id)
        return OUTCOME_SESSION_NOT_FOUND

    if session.get("session_snapshot_hash") != snapshot_hash:
        # 舊 snapshot 的訊息：session 已往前走，不可誤改
        logger.warning(
            "DLQ 訊息的 snapshot 已過期，不修改 session：session_id=%s", session_id
        )
        return OUTCOME_SNAPSHOT_MISMATCH

    status = session.get("batch_status")
    if status == sessions.BATCH_COMPLETED:
        logger.info("DLQ 訊息對應的 batch 已完成，略過：session_id=%s", session_id)
        return OUTCOME_ALREADY_COMPLETED
    if status == sessions.BATCH_FAILED:
        logger.info("DLQ 訊息對應的 batch 已是 failed，略過：session_id=%s", session_id)
        return OUTCOME_ALREADY_FAILED

    sessions.fail_batch(
        elder_id,
        session_id,
        # 不比對 lease owner：DLQ 收斂的前提就是原 worker 已經放棄
        owner=None,
        code="BATCH_RETRIES_EXHAUSTED",
        message="SQS 重試耗盡，已由 DLQ reconciler 收斂為 failed，待人工 replay",
    )
    publish_alert(elder_id, session_id, client=sns_client)
    return OUTCOME_CONVERGED


def publish_alert(elder_id: str, session_id: str, *, client=None) -> bool:
    """發出安全化的告警。

    只帶 ID 與狀態，不帶對話內容或錯誤堆疊裡可能夾帶的逐字稿（PII 最小化）。
    """
    message = json.dumps(
        {
            "alert": "batch_extraction_failed",
            "elder_id": elder_id,
            "session_id": session_id,
            "action": "需人工確認後 replay（failed → pending）",
        },
        ensure_ascii=False,
    )
    if not ALERT_TOPIC_ARN:
        logger.error("batch 收斂為 failed（未設定告警 topic）：%s", message)
        return False
    try:
        (client or get_sns_client()).publish(
            TopicArn=ALERT_TOPIC_ARN, Subject="batch extraction failed", Message=message
        )
        return True
    except Exception:
        logger.exception("發送 batch 失敗告警失敗：session_id=%s", session_id)
        return False
