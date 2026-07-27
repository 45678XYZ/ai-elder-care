"""端到端萃取編排。

對應 aws-hackathon 的 `end_to_end_pipeline`，順序相同、責任邊界不同：

    frozen turns → chunk planner → 概念檢索 → RAC 分類 → HMLC 剪枝 → 動態 schema 組裝
    → single-pass 萃取 → 時間正規化 → canonical key → slot 去重 → （由 handler 寫入 events）

刻意**不寫 DynamoDB**：寫入涉及條件式 Put、lease 與 session 狀態機，屬 batch handler 的
責任。pipeline 保持純運算，才能在沒有 AWS 環境的情況下完整測試，也讓「同一 snapshot 重跑
產生同一組 canonical key」這件事可以離線驗證。

`suspected_routine_lookup` 對應決策 C：batch 萃取到疑似 routine 完成時只在
`structured_detail` 標 `suspected_routine_id`，絕不寫 completion event。
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
from .config import ExtractionConfig
from .dedup import deduplicate
from .extractor import extract_events
from .models import CanonicalEvent, DedupStats
from .pruner import prune_label_hits
from .retriever import ConceptRetriever
from .schema_composer import compose_multi_event
from .taxonomy import Taxonomy
from .temporal import resolve_observed_at

logger = logging.getLogger(__name__)

SuspectedRoutineLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class ChunkOutcome:
    """單一 chunk 的萃取結果與觀測數據。"""

    chunk_id: str
    events: tuple[CanonicalEvent, ...]
    dropped_events: int = 0
    unmatched_predicates: int = 0
    candidate_count: int = 0
    hit_count: int = 0


@dataclass(frozen=True)
class PipelineResult:
    """一個 session 的萃取結果。"""

    session_id: str
    manifest: ChunkManifest
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats = field(default_factory=DedupStats)
    chunk_outcomes: tuple[ChunkOutcome, ...] = ()

    @property
    def metrics(self) -> dict[str, Any]:
        """供 CloudWatch 的觀測指標。"""
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
            "type_distribution": type_distribution,
        }


@dataclass
class ExtractionPipeline:
    """萃取 pipeline；一次 batch 執行建立一個實例。"""

    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    retriever: ConceptRetriever
    client: Any = None
    embedder: Any = None
    segmenter: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None

    # -- 分塊 ---------------------------------------------------------------

    def plan(
        self,
        session_id: str,
        session_snapshot_hash: str,
        turns: Sequence[Turn],
    ) -> ChunkManifest:
        """規劃 chunk；只在 session 尚無 manifest 時呼叫。

        已有 manifest 時應由 handler 用 `manifest_from_entries` 還原重用，
        才能保證 retry、duplicate delivery 與 DLQ replay 的 chunk ID 完全相同。
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

    # -- 單一 chunk ---------------------------------------------------------

    def process_chunk(
        self,
        elder_id: str,
        session_id: str,
        turns: Sequence[Turn],
        chunk: PlannedChunk,
        *,
        elder: Mapping[str, Any] | None = None,
    ) -> ChunkOutcome:
        """跑完一個 chunk 的檢索 → 分類 → 剪枝 → 萃取 → canonical 身分。"""
        transcript = render_chunk_text(turns, chunk)
        evidence_ids = core_turn_ids(turns, chunk)
        reference = reference_datetime_for(turns, chunk)

        candidates = self.retriever.retrieve(transcript, self.config.rac_top_k)
        classification = classify_chunk(
            chunk.chunk_id,
            transcript,
            candidates,
            model_id=self.config.model_for("classifier"),
            client=self.client,
        )
        hits = prune_label_hits(classification.hits, self.taxonomy)
        if not hits:
            logger.info("chunk 無命中標籤，不產生事件：chunk_id=%s", chunk.chunk_id)
            return ChunkOutcome(
                chunk_id=chunk.chunk_id,
                events=(),
                candidate_count=len(candidates),
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

        return ChunkOutcome(
            chunk_id=chunk.chunk_id,
            events=tuple(events),
            dropped_events=extraction.dropped_events,
            unmatched_predicates=unmatched_predicates,
            candidate_count=len(candidates),
            hit_count=len(hits),
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
        """把萃取結果轉成 canonical event；算不出身分就丟棄並告警。"""
        ts = resolve_observed_at(extracted.observed_at, extracted.raw_temporal_expression, reference)
        subject = normalize_subject(extracted.subject, self.lexicon)
        predicate = normalize_predicate(
            extracted.concept_id, extracted.predicate, self.lexicon, self.taxonomy
        )
        if not predicate.value:
            # 沒有謂語就沒有事件身分；寧可丟掉一筆也不要寫入無法去重的事件
            logger.warning(
                "事件缺少可用謂語，已丟棄：chunk_id=%s concept_id=%s",
                chunk.chunk_id,
                extracted.concept_id,
            )
            return None

        key = canonical_event_key(ts, subject, predicate.value, self.config.event_slot_minutes)
        structured = dict(extracted.attributes)

        # 分類與萃取各有一個信心值；對外取較保守的 min，另一個留在 structured_detail
        hit_confidence = classification_confidence.get(extracted.concept_id)
        if hit_confidence is not None:
            structured["classification_confidence"] = hit_confidence
        confidences = [
            value for value in (hit_confidence, extracted.confidence) if value is not None
        ]
        confidence = min(confidences) if confidences else None

        if predicate.via_alias:
            # 記錄 alias 命中，供調校 lexicon（`__other__` 命中率過高代表詞彙需擴充）
            structured["predicate_alias_hit"] = True

        if self.suspected_routine_lookup is not None:
            suspected = self.suspected_routine_lookup(extracted.concept_id, predicate.value, ts)
            if suspected:
                # 決策 C：只標記供摘要層降噪，不寫 completion event、不改 routine 狀態
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

    # -- 整個 session -------------------------------------------------------

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
        """跑完一個 closed session。

        `manifest` 有值時直接重用（retry／duplicate／DLQ replay 路徑），不重新分塊。
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
