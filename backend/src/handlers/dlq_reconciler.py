"""SQS DLQ reconciler Lambda handler。規範見 docs/framework.md。

將 SQS DLQ 重試耗盡的 batch 訊息收斂為 `failed` 並發送安全化告警。

保護機制與處置邏輯：
1. snapshot hash 不符：訊息對應舊 snapshot，session 已往前走；避免誤將健康 session 標為 failed，故僅記錄並 ack
2. 已 completed：遲到的失敗訊息不得覆寫已完成的結果，僅記錄並 ack
3. 已 failed：已處於待人工處理的終態，重複收斂無意義，僅記錄並 ack
4. hash 相符且非終態：條件式標記 failed、清除 lease、寫入安全化錯誤訊息並發送 SNS 告警
5. 訊息處置原則：不論處置結果或是否有例外，一律 ack（Succeed），避免訊息在 DLQ 反覆死迴圈
"""

from typing import Any
import json
import logging
import os

import boto3

from src.shared import metrics, sessions

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
    """取得或初始化 SNS boto3 client（Singleton 模式，避免重複建立連線）。"""
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns")
    return _sns_client


def handler(event, context):
    """SQS DLQ event source 入口；批次處理訊息並寫入 CloudWatch EMF 指標。

    不論單筆收斂成功與否一律傳回處置結果並 ack，防止無效訊息滯留 DLQ 引發重複觸發死迴圈。
    """
    outcomes: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId", "")
        try:
            outcome = process_record(record)
        except Exception:
            # 防禦性擷取例外：不論處理結果為何一律 ack，防止無法收斂的毒丸訊息滯留 DLQ 引發重複炸死
            logger.exception("DLQ 收斂失敗：message_id=%s", message_id)
            outcome = "error"
        metrics.emit_dlq_outcome(outcome)
        outcomes.append({"message_id": message_id, "outcome": outcome})
    return {"outcomes": outcomes}


def process_record(record: dict[str, Any], *, sns_client=None) -> str:
    """處理單筆 DLQ 記錄，驗證對應 session 狀態並執行條件式收斂。

    Returns:
        收斂結果狀態碼（OUTCOME_*），用於指標統計與紀錄追蹤。
    """
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
        # 舊 snapshot 訊息：session 已往前走，若強制改寫會誤將健康 session 標為失敗
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
        # 不比對 lease owner：DLQ 收斂的前提即為原 worker 處理已耗盡重試並放棄 lease
        owner=None,
        code="BATCH_RETRIES_EXHAUSTED",
        message="SQS 重試耗盡，已由 DLQ reconciler 收斂為 failed，待人工 replay",
    )
    publish_alert(elder_id, session_id, client=sns_client)
    return OUTCOME_CONVERGED


def publish_alert(elder_id: str, session_id: str, *, client=None) -> bool:
    """發送 batch 失敗告警至 SNS Topic。

    告警 payload 僅包含識別 ID 與建議處置動作，嚴禁夾帶對話內容或錯誤堆疊（遵守 PII 最小化原則）。
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

