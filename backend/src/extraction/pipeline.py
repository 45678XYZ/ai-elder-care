"""端到端事件萃取編排器 (End-to-End Extraction Pipeline Orchestrator)。

整合對話分塊 (Chunker)、概念檢索 (Retriever)、RAC 分類 (Classifier)、HMLC 剪枝 (Pruner)、
動態 Schema 組裝 (Schema Composer)、Single-Pass 萃取 (Extractor)、時序解析 (Temporal)、
Canonical 身分建構 (Canonical) 與 Slot 去重 (Dedup) 等模組。
架構規範與寫入規則詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

處理流程：
    frozen turns → chunk planner → 概念檢索 → RAC 分類 → HMLC 剪枝 → 動態 schema 組裝
    → single-pass 萃取 → 時間正規化 → canonical key → slot 去重 → （由 handler 寫入 events）

本模組設計目的與核心機制：
- **純記憶體運算與 DB 責任解耦**：本模組**刻意不直接寫入 DynamoDB**。資料庫寫入涉及條件式 PutItem、租約鎖 (Lease) 與 Session 狀態機，屬於批次處理器 (`batch_extractor.py`) 的責任。Pipeline 保持純運算，才能在無視 AWS 環境的狀況下進行單元測試，並離線驗證「同一 Snapshot 重跑必產出相同 Canonical Key」。
- **重試冪等性與 Manifest 重用**：若 Session 已有 ChunkManifest，重試時一律重用舊 Manifest，確保在 SQS Retry、重複派送或 DLQ Replay 下 Chunk ID 保持 100% 相同。
- **貫徹決策 C（Routine 僅標記不改狀態）**：萃取到疑似 Routine 完成事件時，僅於 `structured_detail` 標註 `suspected_routine_id`，絕不改寫 Routine 完成狀態或產生打卡事件。
- **保守信心值與完備觀測指標 (`metrics`)**：取分類與萃取之最小信心值作為事件信心值；提供完整 metrics 輸出供 CloudWatch 儀表板監控。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
import logging

from .canonical import (
    PredicateLexicon,
    canonical_event_key,
    event_id_for,
    normalize_predicate,
    normalize_subject,
)
from .chunk_planner import (
    ChunkManifest,
    PlannedChunk,
    core_turn_ids,
    plan_chunks,
    reference_datetime_for,
    render_chunk_text,
)
from .chunker import Turn, plan_boundaries
from .classifier import classify_chunk
from .config import EXTRACTION_STRUCTURED_OUTPUT, ExtractionConfig
from .dedup import deduplicate
from .extractor import extract_events
from .models import CanonicalEvent, DedupStats
from .pruner import prune_label_hits
from .retriever import ConceptRetriever
from .schema_composer import compose_multi_event
from .taxonomy import Taxonomy
from .temporal import resolve_observed_at

logger = logging.getLogger(__name__)

# 疑似 Routine 完成之查表邏輯型別；傳入 `(concept_id, predicate, ts)` 回傳 `routine_id`
SuspectedRoutineLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class ChunkOutcome:
    """單一 Chunk 的萃取結果與執行期觀測數據容器。"""

    chunk_id: str
    events: tuple[CanonicalEvent, ...]
    dropped_events: int = 0
    unmatched_predicates: int = 0
    candidate_count: int = 0
    hit_count: int = 0
    # Structured Outputs 被降級為 Prompt 指引模式之次數；持續大於 0 代表模型或 SDK 硬約束失效
    structured_output_degraded: int = 0
    model_latency_ms: int = 0


@dataclass(frozen=True)
class PipelineResult:
    """單一 Session 之完整萃取結果與觀測指標容器。"""

    session_id: str
    manifest: ChunkManifest
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats = field(default_factory=DedupStats)
    chunk_outcomes: tuple[ChunkOutcome, ...] = ()

    @property
    def metrics(self) -> dict[str, Any]:
        """組裝供 CloudWatch 電視牆與告警使用的結構化統計指標。"""
        type_distribution: dict[str, int] = {}
        for event in self.events:
            type_distribution[event.type] = type_distribution.get(event.type, 0) + 1
        return {
            "chunk_count": len(self.manifest.chunks),
            "event_count": len(self.events),
            "dropped_events": sum(outcome.dropped_events for outcome in self.chunk_outcomes),
            "unmatched_predicates": sum(
                outcome.unmatched_predicates for outcome in self.chunk_outcomes
            ),
            "dedup_merge_rate": round(self.dedup.merge_rate, 4),
            "dedup_key_merged": self.dedup.key_merged,
            "dedup_alias_merged": self.dedup.alias_merged,
            "chunker_fallback_used": self.manifest.fallback_used,
            "structured_output_degraded": sum(
                outcome.structured_output_degraded for outcome in self.chunk_outcomes
            ),
            "model_latency_ms": sum(outcome.model_latency_ms for outcome in self.chunk_outcomes),
            "type_distribution": type_distribution,
        }


@dataclass
class ExtractionPipeline:
    """端到端萃取編排器主類別；一次批次任務建立單一實例。"""

    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    retriever: ConceptRetriever
    client: Any = None
    embedder: Any = None
    segmenter: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None

    # -- 對話分塊 (Chunking) --------------------------------------------------

    def plan(
        self,
        session_id: str,
        session_snapshot_hash: str,
        turns: Sequence[Turn],
    ) -> ChunkManifest:
        """規劃對話主題分塊與邊界 (Chunk Manifest)。

        僅在 Session 首次執行批次萃取時呼叫；若已有 Manifest，必須重用既有資料以確保 SQS 重試冪等性。
        """
        plan = plan_boundaries(
            turns,
            chunker_type=self.config.chunker_type,
            embedder=self.embedder,
            client=self.client,
            segmenter=self.segmenter,
            model_id=self.config.model_for("chunker"),
        )
        return plan_chunks(
            session_id,
            session_snapshot_hash,
            turns,
            plan.boundaries,
            planner_version=self.config.chunk_planner_version,
            strategy=plan.strategy,
            fallback_used=plan.fallback_used,
        )

    # -- 單一 Chunk 處理 -----------------------------------------------------

    def process_chunk(
        self,
        elder_id: str,
        session_id: str,
        turns: Sequence[Turn],
        chunk: PlannedChunk,
        *,
        elder: Mapping[str, Any] | None = None,
    ) -> ChunkOutcome:
        """執行單一 Chunk 之完整流程：概念檢索 -> RAC 分類 -> HMLC 剪枝 -> 動態 Schema 組裝 -> Single-Pass 萃取 -> Canonical 身分建構。"""
        transcript = render_chunk_text(turns, chunk)
        evidence_ids = core_turn_ids(turns, chunk)
        reference = reference_datetime_for(turns, chunk)

        candidates = self.retriever.retrieve(transcript, self.config.rac_top_k)
        classification = classify_chunk(
            chunk.chunk_id,
            transcript,
            candidates,
            taxonomy=self.taxonomy,
            model_id=self.config.model_for("classifier"),
            client=self.client,
        )
        hits = prune_label_hits(classification.hits, self.taxonomy)
        degraded = 0 if classification.metadata.get("structured_output", True) else 1
        latency = int(classification.metadata.get("latency_ms") or 0)

        if not hits:
            logger.info("chunk 無命中標籤，不產生事件：chunk_id=%s", chunk.chunk_id)
            return ChunkOutcome(
                chunk_id=chunk.chunk_id,
                events=(),
                candidate_count=len(candidates),
                structured_output_degraded=degraded,
                model_latency_ms=latency,
            )

        composed = compose_multi_event(hits, self.taxonomy)
        extraction = extract_events(
            chunk.chunk_id,
            transcript,
            reference,
            composed,
            self.taxonomy,
            predicate_candidates=self.lexicon.candidates_for_prompt(composed.concept_ids),
            elder=elder,
            extraction_mode=self.config.extraction_mode,
            model_id=self.config.model_for("extractor"),
            client=self.client,
        )

        classification_confidence = {hit.concept_id: hit.confidence for hit in hits}
        events: list[CanonicalEvent] = []
        unmatched_predicates = 0
        for extracted in extraction.events:
            built = self._build_canonical_event(
                elder_id=elder_id,
                session_id=session_id,
                chunk=chunk,
                evidence_ids=evidence_ids,
                reference=reference,
                extracted=extracted,
                classification_confidence=classification_confidence,
            )
            if built is None:
                continue
            event, matched_predicate = built
            if not matched_predicate:
                unmatched_predicates += 1
            events.append(event)

        if self.config.extraction_mode == EXTRACTION_STRUCTURED_OUTPUT and not extraction.metadata.get(
            "structured_output", True
        ):
            degraded += 1

        return ChunkOutcome(
            chunk_id=chunk.chunk_id,
            events=tuple(events),
            dropped_events=extraction.dropped_events,
            unmatched_predicates=unmatched_predicates,
            candidate_count=len(candidates),
            hit_count=len(hits),
            structured_output_degraded=degraded,
            model_latency_ms=latency + int(extraction.metadata.get("latency_ms") or 0),
        )

    def _build_canonical_event(
        self,
        *,
        elder_id: str,
        session_id: str,
        chunk: PlannedChunk,
        evidence_ids: Sequence[str],
        reference: str,
        extracted,
        classification_confidence: Mapping[str, float],
    ) -> tuple[CanonicalEvent, bool] | None:
        """將抽取結果轉換為具有唯一 Canonical 身分之 CanonicalEvent 實例。

        若謂語正規化失敗導致無可用謂語，直接丟棄該事件並發出警告（無謂語無法計算 Canonical Key 與進行去重）。
        """
        ts = resolve_observed_at(extracted.observed_at, extracted.raw_temporal_expression, reference)
        subject = normalize_subject(extracted.subject, self.lexicon)
        predicate = normalize_predicate(
            extracted.concept_id, extracted.predicate, self.lexicon, self.taxonomy, embedder=self.embedder
        )
        if not predicate.value:
            # 無謂語則無事件身分；寧可丟棄單一事件，也不寫入無法去重的垃圾資料
            logger.warning(
                "事件缺少可用謂語，已丟棄：chunk_id=%s concept_id=%s",
                chunk.chunk_id,
                extracted.concept_id,
            )
            return None

        key = canonical_event_key(ts, subject, predicate.value, self.config.event_slot_minutes)
        structured = dict(extracted.attributes)

        # 信心值採用保守策略：取分類信心值與萃取信心值之最小值 (`min`)，保留較高風險提示
        hit_confidence = classification_confidence.get(extracted.concept_id)
        if hit_confidence is not None:
            structured["classification_confidence"] = hit_confidence
        confidences = [
            value for value in (hit_confidence, extracted.confidence) if value is not None
        ]
        confidence = min(confidences) if confidences else None

        if predicate.via_alias:
            structured["predicate_alias_hit"] = True
        elif predicate.via_fuzzy_embedding:
            structured["predicate_fuzzy_hit"] = True
            structured["predicate_fuzzy_sim"] = predicate.similarity_score
        elif not predicate.matched:
            structured["is_novel_predicate"] = True

        if predicate.raw_predicate and predicate.raw_predicate != predicate.value:
            structured["raw_predicate"] = predicate.raw_predicate

        if self.suspected_routine_lookup is not None:
            suspected = self.suspected_routine_lookup(extracted.concept_id, predicate.value, ts)
            if suspected:
                # 貫徹決策 C：僅標註 suspected_routine_id 供摘要層降躁，絕不寫入完成事件或更動 Routine 狀態
                structured["suspected_routine_id"] = suspected

        event = CanonicalEvent(
            elder_id=elder_id,
            event_id=event_id_for(elder_id, key),
            canonical_event_key=key,
            ts=ts,
            type=self.taxonomy.high_level_type(extracted.concept_id),
            concept_id=extracted.concept_id,
            taxonomy_version=self.config.taxonomy_version or self.taxonomy.taxonomy_version,
            subject=subject,
            predicate=predicate.value,
            detail=extracted.summary,
            structured_detail=structured,
            confidence=confidence,
            session_id=session_id,
            source_chunk_id=chunk.chunk_id,
            conversation_id=evidence_ids[0] if evidence_ids else None,
            evidence_conversation_ids=tuple(evidence_ids),
        )
        return event, predicate.matched

    # -- 整個 Session 執行 ---------------------------------------------------

    def run(
        self,
        elder_id: str,
        session_id: str,
        session_snapshot_hash: str,
        turns: Sequence[Turn],
        *,
        manifest: ChunkManifest | None = None,
        elder: Mapping[str, Any] | None = None,
    ) -> PipelineResult:
        """執行整個 Closed Session 之事件萃取。

        全 Chunk 事件收集完成後，於 Session 層級發起時間桶與同義詞去重 (`deduplicate`)，產出最終結果與統計資料。
        """
        resolved_manifest = manifest or self.plan(session_id, session_snapshot_hash, turns)

        outcomes: list[ChunkOutcome] = []
        collected: list[CanonicalEvent] = []
        for chunk in resolved_manifest.chunks:
            outcome = self.process_chunk(elder_id, session_id, turns, chunk, elder=elder)
            outcomes.append(outcome)
            collected.extend(outcome.events)

        events, stats = deduplicate(
            collected,
            slot_minutes=self.config.event_slot_minutes,
            lexicon=self.lexicon,
        )
        return PipelineResult(
            session_id=session_id,
            manifest=resolved_manifest,
            events=events,
            dedup=stats,
            chunk_outcomes=tuple(outcomes),
        )
