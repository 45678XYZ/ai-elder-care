"""TTS 合成 worker Lambda (SQS Consumer)。

TTS 從 chat 的同步路徑搬出來的理由是硬性的：自建模型（BreezyVoice、OmniVoice）合成一段
回覆要數十秒到數分鐘，而 `POST /chat` 走 API Gateway REST，整條請求的上限是 29 秒。
在同步路徑上，這些 provider 永遠等不到、永遠 fallback，等於白建。

更糟的是逾時不會取消：SageMaker 不會因為呼叫端斷線就停止推論，容器仍會把那段合成做完。
每個逾時的 chat 請求都在序列化的 endpoint 上多排一份沒人收得到的工作，積壓只會愈來愈深。

因此 chat 只負責把「要合成什麼」丟進佇列並立刻回文字，實際合成由本 worker 完成——
Lambda 最長可跑 15 分鐘，不受 29 秒限制——再把 MP3 寫進 chat 已經約定好的 S3 key。
App 拿到的 presigned URL 在音訊就緒前是 404，就緒後即可播放。

處理原則：

- **冪等**：object key 由 conversation_id 決定。SQS 至少投遞一次，重複訊息在物件已存在時
  直接 ACK，不重跑一次昂貴的合成。
- **錯誤分流**：provider 可重試的失敗交回 SQS（耗盡後進 DLQ）；契約層面不可能成功的
  （語言不支援、route 未核准、文字過長）直接 ACK，不在佇列裡死循環。
- **不記錄長者內容**：log 只留長度、類別與 correlation id，與同步路徑的規則一致。
"""

from typing import Any
import json
import logging
import os
import time

import boto3

from src.shared import turns
from src.shared.tts import (
    CancellationSignal as TtsCancellationSignal,
    CorrelationContext as TtsCorrelationContext,
    Deadline as TtsDeadline,
    HakkaDialect,
    Language as TtsLanguage,
    SynthesizedAudio,
    TtsErrorCategory,
    get_tts_facade,
)

logger = logging.getLogger(__name__)

S3_BUCKET_NAME = os.environ.get("S3_AUDIO_BUCKET", "e-hakka-care-audio")

# 合成預算。Lambda 本身的 timeout 必須大於這個值，否則函數會先被砍掉，訊息回到佇列後
# 又從頭合成一次——重試的成本在這裡是好幾分鐘的 GPU 時間。
SYNTHESIS_BUDGET_SECONDS = float(os.environ.get("TTS_WORKER_BUDGET_SECONDS", "540"))

# 這些類別再投幾次也不會成功：設定沒核准、語言不支援、文字不合法。
# 交回 SQS 只會佔用佇列並最終進 DLQ，因此直接 ACK 並留下 log。
_PERMANENT_CATEGORIES = frozenset(
    {
        TtsErrorCategory.ROUTE_NOT_APPROVED,
        TtsErrorCategory.UNSUPPORTED_LANGUAGE,
        TtsErrorCategory.UNSUPPORTED_DIALECT,
        TtsErrorCategory.INVALID_TEXT,
    }
)

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


class PermanentSynthesisError(Exception):
    """再投遞也不會成功；ACK 掉並留下 log。"""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS 觸發入口點；採用 Partial Batch Failures 語法處理。"""
    del context
    failures: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId", "")
        try:
            process_record(record)
        except PermanentSynthesisError as exc:
            logger.error("TTS 永久失敗，不再重投：message_id=%s %s", message_id, exc)
            _mark_unavailable(record)
        except Exception:
            # 不記錄 raw exception：可能含 endpoint、回覆文字或 SDK response。
            logger.warning("TTS 暫時失敗，交回 SQS 重投：message_id=%s", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def _mark_unavailable(record: dict[str, Any]) -> None:
    """永久失敗後把 turn 的 pending 標記收掉，讓 App 停止等待。

    這一步本身失敗只記 log：訊息已經 ACK，重投也救不回來，而 turn 的文字內容是完整的。
    App 最壞的情況是等到 presigned URL 過期為止。
    """
    try:
        body = json.loads(record.get("body") or "{}")
        elder_id = body.get("elder_id")
        conversation_id = body.get("conversation_id")
        if isinstance(elder_id, str) and isinstance(conversation_id, str):
            turns.clear_audio_pending(elder_id, conversation_id)
    except Exception:
        logger.warning("清除 TTS pending 標記失敗；App 需等 URL 過期後自行放棄")


def parse_message(record: dict[str, Any]) -> dict[str, Any]:
    """解析 chat 送出的合成工作。缺欄位一律視為永久失敗。"""
    try:
        body = json.loads(record.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise PermanentSynthesisError("message body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise PermanentSynthesisError("message body is not an object")

    for field in ("elder_id", "conversation_id", "object_key", "text", "language"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PermanentSynthesisError(f"message is missing {field}")
    return body


def audio_already_stored(object_key: str) -> bool:
    """物件已存在代表這則訊息是重複投遞，不必再合成一次。"""
    try:
        get_s3_client().head_object(Bucket=S3_BUCKET_NAME, Key=object_key)
    except Exception:
        # 包含 404。分不出「不存在」與「暫時性錯誤」時一律當作要合成：
        # 多做一次的代價是 GPU 時間，漏做的代價是長者永遠聽不到這句回覆。
        return False
    return True


def process_record(record: dict[str, Any]) -> None:
    message = parse_message(record)
    conversation_id = message["conversation_id"]
    object_key = message["object_key"]
    correlation_id = message.get("correlation_id", "")

    if audio_already_stored(object_key):
        logger.info(
            "TTS 音訊已存在，略過重複訊息：conversation_id=%s", conversation_id
        )
        return

    try:
        language = TtsLanguage.from_str(message["language"])
    except (ValueError, KeyError) as exc:
        raise PermanentSynthesisError("unsupported language") from exc

    dialect = None
    raw_dialect = message.get("dialect")
    if raw_dialect:
        try:
            dialect = HakkaDialect(raw_dialect)
        except ValueError as exc:
            raise PermanentSynthesisError("unsupported dialect") from exc

    started = time.monotonic()
    result = get_tts_facade().synthesize(
        text=message["text"],
        language=language,
        dialect=dialect,
        deadline=TtsDeadline.after(SYNTHESIS_BUDGET_SECONDS, time.monotonic),
        cancellation=TtsCancellationSignal(),
        context=TtsCorrelationContext(correlation_id=correlation_id),
    )
    elapsed = time.monotonic() - started

    if not isinstance(result, SynthesizedAudio):
        category = getattr(result.category, "value", "internal_error")
        if result.category in _PERMANENT_CATEGORIES:
            raise PermanentSynthesisError(f"category={category}")
        raise RuntimeError(f"retryable TTS failure category={category}")

    get_s3_client().put_object(
        Bucket=S3_BUCKET_NAME,
        Key=object_key,
        Body=result.data,
        ContentType="audio/mpeg",
    )

    # 順序不能顛倒：turn 帶著 key 就代表「這句話有音檔」，先寫 key 再上傳的話，
    # 兩者之間任何失敗都會留下一個永遠簽出死連結的 turn。
    attached = turns.attach_audio_key(
        message["elder_id"], conversation_id, object_key
    )

    logger.info(
        "TTS 合成完成：conversation_id=%s chars=%d bytes=%d elapsed=%.1fs attached=%s",
        conversation_id,
        len(message["text"]),
        len(result.data),
        elapsed,
        attached,
    )
