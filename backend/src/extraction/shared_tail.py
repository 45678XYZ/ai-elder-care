"""所有萃取流程共用的寫入前收斂尾段（Shared Tail）。

事件草稿（ExtractedEvent）在放入 PipelineResult 之前，經過：
1. 時間解析（temporal.resolve_observed_at）
2. canonical key／event_id 身分計算
3. slot 去重（dedup.deduplicate）
4. 事件型別驗證
"""

from collections.abc import Callable, Mapping
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
from .config import ExtractionConfig
from .dedup import deduplicate
from .models import CanonicalEvent, DedupStats, ExtractedEvent
from .taxonomy import Taxonomy
from .temporal import resolve_observed_at

logger = logging.getLogger(__name__)

_MARKER_FIELDS: frozenset[str] = frozenset(
    {"classification_confidence", "raw_predicate", "suspected_routine_id"}
)

_EXCLUDED_GLOBAL_PROPERTIES: frozenset[str] = frozenset({"source_utterance"})

SuspectedRoutineLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class EventOrigin:
    """一批事件草稿的共同來源脈絡。"""

    reference_datetime: str
    evidence_conversation_ids: tuple[str, ...]
    source_chunk_id: str | None = None
    classification_confidence: float | None = None


@dataclass(frozen=True)
class TailResult:
    """SharedTail.finalize() 的輸出。"""

    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats
    dropped_events: int = 0
    unmatched_predicates: int = 0


@dataclass
class SharedTail:
    """有狀態尾段累積器：逐筆 absorb，最後一次 finalize。"""

    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    embedder: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None
    family_aliases: Mapping[str, str] = field(default_factory=dict)

    _drafts: list[CanonicalEvent] = field(default_factory=list, init=False, repr=False)
    _dropped_events: int = field(default=0, init=False, repr=False)
    _unmatched_predicates: int = field(default=0, init=False, repr=False)

    def absorb(
        self,
        *,
        elder_id: str,
        session_id: str,
        extracted: ExtractedEvent,
        origin: EventOrigin,
    ) -> bool:
        ts = resolve_observed_at(
            extracted.observed_at, extracted.raw_temporal_expression, origin.reference_datetime
        )

        ref_date = origin.reference_datetime.split("T")[0]
        ts_date = ts.split("T")[0]
        if ts_date < ref_date:
            logger.info(
                "過往歷史回憶事件已排除：ts=%s ref_date=%s concept_id=%s predicate=%s",
                ts, ref_date, extracted.concept_id, extracted.predicate,
            )
            self._dropped_events += 1
            return False

        subject = normalize_subject(extracted.subject, self.lexicon, extra_aliases=self.family_aliases)

        predicate = normalize_predicate(
            extracted.concept_id,
            extracted.predicate,
            self.lexicon,
            self.taxonomy,
            embedder=self.embedder,
        )
        if not predicate.value:
            logger.warning(
                "事件缺少可用謂語，已丟棄：source_chunk_id=%s concept_id=%s",
                origin.source_chunk_id,
                extracted.concept_id,
            )
            self._dropped_events += 1
            return False

        key = canonical_event_key(ts, subject, predicate.value, self.config.event_slot_minutes)
        structured = dict(extracted.attributes)

        hit_confidence = origin.classification_confidence
        if hit_confidence is not None:
            structured["classification_confidence"] = hit_confidence
        confidences = [
            value for value in (hit_confidence, extracted.confidence) if value is not None
        ]
        confidence = min(confidences) if confidences else None

        if predicate.raw_predicate and predicate.raw_predicate != predicate.value:
            structured["raw_predicate"] = predicate.raw_predicate

        if self.suspected_routine_lookup is not None:
            suspected = self.suspected_routine_lookup(extracted.concept_id, predicate.value, ts)
            if suspected:
                structured["suspected_routine_id"] = suspected

        evidence_ids = origin.evidence_conversation_ids
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
            source_chunk_id=origin.source_chunk_id,
            conversation_id=evidence_ids[0] if evidence_ids else None,
            evidence_conversation_ids=tuple(evidence_ids),
        )
        self._drafts.append(event)
        if not predicate.matched:
            self._unmatched_predicates += 1
        return predicate.matched

    def finalize(self) -> TailResult:
        deduped_events, dedup_stats = deduplicate(
            self._drafts,
            slot_minutes=self.config.event_slot_minutes,
            lexicon=self.lexicon,
            embedder=self.embedder,
        )

        valid_events: list[CanonicalEvent] = []
        for event in deduped_events:
            if _validate_event(event, self.taxonomy):
                valid_events.append(event)
            else:
                logger.warning(
                    "事件未通過型別驗證，已丟棄：event_id=%s concept_id=%s type=%s",
                    event.event_id,
                    event.concept_id,
                    event.type,
                )
                self._dropped_events += 1

        return TailResult(
            events=tuple(valid_events),
            dedup=dedup_stats,
            dropped_events=self._dropped_events,
            unmatched_predicates=self._unmatched_predicates,
        )


def _global_property_names(taxonomy: Taxonomy) -> frozenset[str]:
    names = {
        name
        for prop in (taxonomy.property_registry.get("global_properties") or [])
        if (name := prop.get("name")) and name not in _EXCLUDED_GLOBAL_PROPERTIES
    }
    return frozenset(names)


def _node_own_property_names(taxonomy: Taxonomy, concept_id: str) -> frozenset[str]:
    node_properties = taxonomy.property_registry.get("node_properties") or {}
    props = node_properties.get(concept_id)
    if not isinstance(props, list):
        return frozenset()
    return frozenset(name for prop in props if (name := prop.get("name")))


def _ancestor_chain_including_self(taxonomy: Taxonomy, concept_id: str) -> tuple[str, ...]:
    chain = [concept_id, *taxonomy.ancestors(concept_id)]
    ordered = tuple(reversed([cid for cid in chain if taxonomy.get(cid) is not None]))
    if ordered and taxonomy.nodes[ordered[0]].level == 0:
        return ordered[1:]
    return ordered


def _allowed_structured_detail_keys(taxonomy: Taxonomy, concept_id: str) -> frozenset[str]:
    if taxonomy.is_pseudo_concept(concept_id):
        return _MARKER_FIELDS
    allowed = set(_MARKER_FIELDS) | _global_property_names(taxonomy)
    for node_id in _ancestor_chain_including_self(taxonomy, concept_id):
        allowed |= _node_own_property_names(taxonomy, node_id)
    return frozenset(allowed)


def _validate_event(event: CanonicalEvent, taxonomy: Taxonomy) -> bool:
    if taxonomy.get(event.concept_id) is None:
        return False
    if event.type not in taxonomy.type_ids:
        return False
    if not (event.ts and event.subject and event.predicate and event.detail):
        return False
    allowed = _allowed_structured_detail_keys(taxonomy, event.concept_id)
    for key in event.structured_detail or {}:
        if key not in allowed:
            return False
    return True
