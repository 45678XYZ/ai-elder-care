"""批次事件萃取器 Lambda Handler (SQS Consumer)。

負責處理 SQS 訊息佇列觸發之非同步事件萃取任務，將 SQS「至少一次投遞 (At-Least-Once Delivery)」
轉譯為資料庫層級「恰好一次 (Exactly-Once)」之可見結果。
架構規範、狀態機轉移、租約與 DLQ 處置詳見 `docs/framework.md` 的「Session close、SQS recovery 與 DLQ」。

處理流程：
    claim (pending 或 lease 過期) -> 重用或建立 manifest -> pipeline 萃取 -> 條件式 Put events
    -> 更新 turn batch 欄位 -> complete (清理 GSI 與清除 lease 租約)

本模組設計目的與核心機制：
- **重複投遞處置策略 (不重複重跑)**：
  1. `completed`：已有完成紀錄，直接 ACK 吞下訊息。
  2. `failed`：已標記為永久失敗，直接 ACK 吞下訊息（等待人工排除後重播）。
  3. `snapshot_hash` 不符：訊息為舊版本對話快照，直接 ACK 吞下訊息。
  4. `lease` 仍有效：已有其他 Worker 鎖定處理中，直接 ACK 吞下訊息（由原 Owner 繼續收斂）。
- **錯誤雙軌分流處理 (Error Categorization)**：
  - 永久失敗 (Permanent Error)：資料不符合規範或權限問題，標記 `failed` 狀態後回傳 ACK，防範無效訊息於 SQS 死循環浪費資源。
  - 暫時失敗 (Retryable Error)：Bedrock 節流或資料庫競爭衝突，釋放租約 (`release_batch_lease`) 並丟出例外，交給 SQS 依 Redrive Policy 重投，耗盡後進 DLQ。
- **部分批次失敗語法 (Partial Batch Failures)**：傳回 `{"batchItemFailures": [...]}`，僅對真正的失敗訊息識別碼發起重投，避免整批全數重算。
"""

from typing import Any
import json
import logging
import os
import time
import uuid

from src.extraction.canonical import load_predicate_lexicon
from src.extraction.chunk_planner import ChunkManifest, manifest_from_entries
from src.extraction.chunker import Turn
from src.extraction.config import ExtractionConfig
from src.extraction.pipeline import ExtractionPipeline
from src.extraction.retriever import ConceptRetriever
from src.extraction.segmenter import load_segmenter
from src.extraction.taxonomy import load_taxonomy
from src.shared import bedrock, db, metrics, sessions

logger = logging.getLogger(__name__)

# 分佈式租約 (Lease) 超時時間；設定為 300 秒（大於單一 Session 處理上限），防範 Worker 處理中遭他人誤判為死亡搶佔
BATCH_LEASE_SECONDS = int(os.environ.get("BATCH_LEASE_SECONDS", "300"))


class PermanentBatchError(Exception):
    """資料不一致、結構毀損等無法透過自動重試排除之永久性錯誤。"""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS 觸發入口點；採用 Partial Batch Failures 語法處理。"""
    failures: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId", "")
        try:
            process_record(record, context=context)
        except bedrock.RetryableBedrockError as exc:
            logger.warning("batch 暫時性失敗，交回 SQS 重投：message_id=%s %s", message_id, exc)
            failures.append({"itemIdentifier": message_id})
        except db.DBError as exc:
            # 資料層之暫時性錯誤（如節流或條件競爭）交回 SQS 重投
            logger.warning("batch 資料層失敗，交回 SQS 重投：message_id=%s %s", message_id, exc)
            failures.append({"itemIdentifier": message_id})
        except PermanentBatchError as exc:
            logger.error("batch 永久失敗，已標記 failed：message_id=%s %s", message_id, exc)
        except Exception:
            logger.exception("batch 未預期錯誤，交回 SQS 重投：message_id=%s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def parse_message(record: dict[str, Any]) -> tuple[str, str, str]:
    """從 SQS Record 內解析出 `(elder_id, session_id, session_snapshot_hash)` 三元組。

    三個欄位均為強制的基底身分識別；缺少 snapshot hash 將無法與目前 frozen snapshot 比對，無法安全處置重複訊息。
    """
    try:
        body = json.loads(record.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise PermanentBatchError(f"SQS body 不是合法 JSON：{exc}") from exc

    elder_id = body.get("elder_id")
    session_id = body.get("session_id")
    snapshot_hash = body.get("session_snapshot_hash")
    if not (elder_id and session_id and snapshot_hash):
        raise PermanentBatchError("SQS 訊息缺少 elder_id／session_id／session_snapshot_hash")
    return elder_id, session_id, snapshot_hash


def build_pipeline(config: ExtractionConfig) -> ExtractionPipeline:
    """組裝 ExtractionPipeline 編排器物件。

    相關 Taxonomy 與 Lexicon 資產載入設有內部快取，在 Lambda 熱啟動 (Warm Start) 下不會重複讀取磁碟。
    """
    taxonomy = load_taxonomy(config.taxonomy_assets_dir)
    lexicon = load_predicate_lexicon(config.taxonomy_assets_dir)
    embedder = bedrock.BedrockEmbeddingProvider(config.embedding_model_id, config.embedding_dim)
    retriever = ConceptRetriever(
        taxonomy,
        embedder,
        top_k=config.rac_top_k,
        vector_bucket=config.concept_vector_bucket,
        index_name=config.concept_vector_index,
        s3vectors_client=_s3vectors_client() if config.concept_vector_bucket else None,
    )
    return ExtractionPipeline(
        config=config,
        taxonomy=taxonomy,
        lexicon=lexicon,
        retriever=retriever,
        embedder=embedder,
        segmenter=load_segmenter(config.segmenter_assets_dir),
    )


_s3vectors = None


def _s3vectors_client():
    """惰性初始化 S3 Vectors boto3 client。"""
    global _s3vectors
    if _s3vectors is None:
        import boto3

        _s3vectors = boto3.client("s3vectors")
    return _s3vectors


def process_record(record: dict[str, Any], *, context=None, pipeline=None) -> str:
    """處理單一 SQS Record 訊息；傳回處置狀態字串供測試斷言與監控。"""
    started = time.monotonic()
    elder_id, session_id, snapshot_hash = parse_message(record)
    owner = _lease_owner(context)

    outcome, session = sessions.claim_batch(
        elder_id,
        session_id,
        snapshot_hash=snapshot_hash,
        owner=owner,
        lease_seconds=BATCH_LEASE_SECONDS,
    )
    if outcome != sessions.CLAIM_ACQUIRED:
        # 四種重複投遞或無效情境直接 ACK，不重複計算亦不更動資料庫狀態
        logger.info(
            "batch 不需處理，直接 ack：session_id=%s outcome=%s", session_id, outcome
        )
        metrics.emit_batch_outcome(outcome, session_id=session_id)
        return outcome

    config = ExtractionConfig.from_env()
    resolved_pipeline = pipeline or build_pipeline(config)

    try:
        result = _run_extraction(resolved_pipeline, config, elder_id, session_id, session)
    except PermanentBatchError as exc:
        sessions.fail_batch(
            elder_id, session_id, owner=owner, code="PERMANENT_ERROR", message=str(exc)
        )
        metrics.emit_batch_outcome("permanent_failure", session_id=session_id)
        raise
    except bedrock.PermanentBedrockError as exc:
        sessions.fail_batch(
            elder_id, session_id, owner=owner, code="MODEL_PERMANENT_ERROR", message=str(exc)
        )
        metrics.emit_batch_outcome("model_permanent_failure", session_id=session_id)
        raise PermanentBatchError(str(exc)) from exc
    except Exception:
        # 暫時性失敗主動釋放租約，讓重投訊息或定時掃描 Recovery Sweep 能立刻接手
        sessions.release_batch_lease(elder_id, session_id, owner=owner)
        metrics.emit_batch_outcome("retryable_failure", session_id=session_id)
        raise

    written, conflicts = _write_events(result)
    if conflicts:
        # 事件內容互斥代表既存資料與本次萃取產生嚴重矛盾，需留存並由人工排查
        sessions.fail_batch(
            elder_id,
            session_id,
            owner=owner,
            code="EVENT_CONFLICT",
            message=f"{len(conflicts)} 筆事件內容互斥：{conflicts[:3]}",
        )
        metrics.emit_batch_outcome("event_conflict", session_id=session_id)
        raise PermanentBatchError(f"事件內容互斥：{conflicts[:3]}")

    sessions.mark_turns_batch_completed(
        elder_id,
        _chunk_by_turn(result),
        extractor_version=config.batch_extractor_version,
    )
    sessions.complete_batch(
        elder_id,
        session_id,
        owner=owner,
        extractor_version=config.batch_extractor_version,
    )

    logger.info(
        "batch 完成：session_id=%s events=%s metrics=%s",
        session_id,
        written,
        json.dumps(result.metrics, ensure_ascii=False),
    )
    metrics.emit_pipeline_metrics(
        result.metrics,
        elder_id=elder_id,
        session_id=session_id,
        chunker_type=config.chunker_type,
    )
    metrics.emit_batch_outcome(
        sessions.CLAIM_ACQUIRED,
        attempts=int(session.get("batch_attempts") or 0),
        duration_ms=int((time.monotonic() - started) * 1000),
        session_id=session_id,
    )
    return sessions.CLAIM_ACQUIRED


def _lease_owner(context: Any) -> str:
    """產生本次執行的專屬 Owner 識別碼，區分「同個 Worker 續約」與「異地接管者」。"""
    request_id = getattr(context, "aws_request_id", None)
    return request_id or f"local-{uuid.uuid4().hex[:12]}"


def _run_extraction(
    pipeline: ExtractionPipeline,
    config: ExtractionConfig,
    elder_id: str,
    session_id: str,
    session: dict[str, Any],
) -> Any:
    turn_ids = session.get("turn_ids") or []
    if not turn_ids:
        raise PermanentBatchError("frozen session 沒有 turn_ids")

    raw_turns = sessions.get_frozen_turns(elder_id, turn_ids)
    turns = tuple(_to_turn(raw) for raw in raw_turns)
    snapshot_hash = session["session_snapshot_hash"]

    existing_manifest = session.get("chunk_manifest")
    if existing_manifest:
        manifest: ChunkManifest | None = manifest_from_entries(
            session_id,
            snapshot_hash,
            session.get("chunk_planner_version") or config.chunk_planner_version,
            existing_manifest,
        )
    else:
        planned = pipeline.plan(session_id, snapshot_hash, turns)
        stored = sessions.persist_chunk_manifest(
            elder_id,
            session_id,
            planned.to_manifest(),
            planner_version=config.chunk_planner_version,
        )
        # 條件式寫入競爭下若其他 Worker 先寫入，以既存版本為準，確保 Chunk ID 全局一致
        manifest = manifest_from_entries(
            session_id, snapshot_hash, config.chunk_planner_version, stored
        )

    elder = db.get_elder(elder_id)
    return pipeline.run(
        elder_id,
        session_id,
        snapshot_hash,
        turns,
        manifest=manifest,
        elder=elder,
    )


def _to_turn(raw: dict[str, Any]) -> Turn:
    """將對話紀錄的 Turn Item 轉換為分塊器所需要的最小資料結構。

    Speaker 判斷機制：若包含長者逐字稿 `elder_transcript` 則發言者判定為「長者」；否則判定為「AI」。
    """
    elder_text = (raw.get("elder_transcript") or "").strip()
    ai_text = (raw.get("ai_respond_text") or raw.get("ai_prompt_text") or "").strip()
    if elder_text:
        speaker, text = "長者", elder_text
    else:
        speaker, text = "AI", ai_text
    return Turn(
        conversation_id=raw.get("conversation_id") or raw["record_id"].split("#", 1)[-1],
        speaker=speaker,
        text=text,
        created_at=raw["created_at"],
    )


def _write_events(result: Any) -> tuple[int, list[str]]:
    """透過條件式 Put 將事件寫入 DynamoDB；遇到完全相同之 `event_id` 與內容視為冪等成功。"""
    written = 0
    conflicts: list[str] = []
    for event in result.events:
        try:
            db.create_event(event.to_event_item())
            written += 1
        except db.EventConflictError as exc:
            conflicts.append(exc.event_id)
    return written, conflicts


def _chunk_by_turn(result: Any) -> dict[str, str]:
    """建立 Turn ID 至初建 Chunk ID 之對照表；供更新 Turn 關聯狀態。"""
    mapping: dict[str, str] = {}
    for outcome in result.chunk_outcomes:
        for event in outcome.events:
            for turn_id in event.evidence_conversation_ids:
                mapping.setdefault(turn_id, outcome.chunk_id)
    return mapping
