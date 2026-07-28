"""batch extractor Lambda（SQS consumer）。

規範見 docs/framework.md 的「Session close、SQS recovery 與 DLQ」。這支 handler 的責任是
把「至少一次投遞」轉成「恰好一次的可見結果」：

    claim（pending 或 lease 過期）→ 重用或建立 manifest → pipeline → conditional Put events
    → 更新 turn batch 欄位 → complete（清 GSI 與 lease）

重複投遞的處置分四種，全部不重跑：`completed` 直接 ack、`failed` 直接 ack（等人工 replay）、
snapshot hash 不符直接 ack（訊息對應的是舊 snapshot）、lease 仍有效直接 ack（讓原 owner 收斂）。

失敗分兩類：permanent（驗證、權限、資料不一致）標 `failed` 後 ack；retryable（節流、暫時性
故障）放掉 lease 後 throw，讓 SQS 依 redrive 政策重投，耗盡後進 DLQ。

回應使用 partial batch failure：只回報真正需要重投的 messageId，避免整批重跑。
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

# lease 長度應大於單一 session 的處理時間上限；過短會讓別人誤判為死亡而重複處理
BATCH_LEASE_SECONDS = int(os.environ.get("BATCH_LEASE_SECONDS", "300"))


class PermanentBatchError(Exception):
    """資料不一致等無法靠重試解決的錯誤；標 failed 後 ack。"""


def handler(event, context):
    """SQS event source 入口。"""
    failures: list[dict[str, str]] = []
    for record in event.get("Records") or []:
        message_id = record.get("messageId", "")
        try:
            process_record(record, context=context)
        except bedrock.RetryableBedrockError as exc:
            logger.warning("batch 暫時性失敗，交回 SQS 重投：message_id=%s %s", message_id, exc)
            failures.append({"itemIdentifier": message_id})
        except db.DBError as exc:
            # 資料層的暫時性錯誤（節流、條件競爭）同樣交回重投
            logger.warning("batch 資料層失敗，交回 SQS 重投：message_id=%s %s", message_id, exc)
            failures.append({"itemIdentifier": message_id})
        except PermanentBatchError as exc:
            logger.error("batch 永久失敗，已標記 failed：message_id=%s %s", message_id, exc)
        except Exception:
            logger.exception("batch 未預期錯誤，交回 SQS 重投：message_id=%s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def parse_message(record: dict[str, Any]) -> tuple[str, str, str]:
    """取出 `(elder_id, session_id, session_snapshot_hash)`。

    三個值都必填：少了 snapshot hash 就無法判斷這則訊息是否對應目前的 frozen snapshot，
    也就無法安全地處理 duplicate 與 DLQ replay。
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
    """組裝 pipeline；資產載入有快取，warm start 不重複讀檔。"""
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
    global _s3vectors
    if _s3vectors is None:
        import boto3

        _s3vectors = boto3.client("s3vectors")
    return _s3vectors


def process_record(record: dict[str, Any], *, context=None, pipeline=None) -> str:
    """處理單一訊息；回傳處置結果字串供測試與觀測。"""
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
        # 四種重複投遞情境都直接 ack，不重跑也不改狀態
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
        # 暫時性失敗放掉 lease，讓重投或 recovery sweep 立刻能接手
        sessions.release_batch_lease(elder_id, session_id, owner=owner)
        metrics.emit_batch_outcome("retryable_failure", session_id=session_id)
        raise

    written, conflicts = _write_events(result)
    if conflicts:
        # 內容互斥代表既有資料與這次萃取矛盾，屬需要人看的問題
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


def _lease_owner(context) -> str:
    """lease owner 用本次執行的識別碼，才能分辨「同一個 worker」與「接管者」。"""
    request_id = getattr(context, "aws_request_id", None)
    return request_id or f"local-{uuid.uuid4().hex[:12]}"


def _run_extraction(pipeline, config, elder_id: str, session_id: str, session: dict[str, Any]):
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
        # 競爭下別人先寫入時以既存版本為準，確保 chunk ID 一致
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
    """把 conversations 的 turn item 轉成分塊與萃取需要的完整對話形狀。

    完整組合 ai_prompt_text (AI 1)、elder_transcript (長者) 與 ai_respond_text (AI 2)，
    確保問答脈絡與用藥警示不遺失。
    """
    parts = []
    ai_prompt = (raw.get("ai_prompt_text") or "").strip()
    elder_text = (raw.get("elder_transcript") or "").strip()
    ai_respond = (raw.get("ai_respond_text") or "").strip()

    if ai_prompt:
        parts.append(f"AI: {ai_prompt}")
    if elder_text:
        parts.append(f"長者: {elder_text}")
    if ai_respond:
        parts.append(f"AI: {ai_respond}")

    text = "\n".join(parts) if parts else (elder_text or ai_respond or ai_prompt or "")
    speaker = "長者" if elder_text else "AI"

    return Turn(
        conversation_id=raw.get("conversation_id") or raw["record_id"].split("#", 1)[-1],
        speaker=speaker,
        text=text,
        created_at=raw["created_at"],
    )


def _write_events(result) -> tuple[int, list[str]]:
    """以條件式 Put 寫入事件；命中相同 canonical 視為冪等。"""
    written = 0
    conflicts: list[str] = []
    for event in result.events:
        try:
            db.create_event(event.to_event_item())
            written += 1
        except db.EventConflictError as exc:
            conflicts.append(exc.event_id)
    return written, conflicts


def _chunk_by_turn(result) -> dict[str, str]:
    """turn → 初建 chunk 的對照；context-only turn 不列入。"""
    mapping: dict[str, str] = {}
    for outcome in result.chunk_outcomes:
        for event in outcome.events:
            for turn_id in event.evidence_conversation_ids:
                mapping.setdefault(turn_id, outcome.chunk_id)
    return mapping
