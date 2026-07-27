"""記憶體內 slot 去重。

規範見 docs/framework.md 的「Batch 記憶體內去重」：session 關閉後輸入已凍結，因此去重是
確定性的，retry 冪等。合併規則：

- 同一 `canonical_event_key`（`Date + Slot + Subject + Predicate`）收斂為一筆。
- `detail` 與 `structured_detail` 取最完整的一次，`evidence_conversation_ids` 取聯集。
- 額外一層 **predicate alias fallback**（feature 文件 §5 第 3 層）：同 slot、同 subject、
  同 `concept_id` 但謂語正規化後仍不同者，以 lexicon 的 canonical 值合併。受控詞彙與
  server-owned 正規化擋不掉的漂移，最後由這層兜住。

跨 session 的殘留重複不在這裡處理，framework 明文不宣稱 zero duplicate。
"""

from collections.abc import Sequence
from dataclasses import replace
import logging

from .canonical import canonical_event_key, event_id_for
from .models import CanonicalEvent, DedupStats

logger = logging.getLogger(__name__)


def _completeness(event: CanonicalEvent) -> tuple[int, int, float]:
    """「最完整」的排序依據：結構化屬性數 → 描述長度 → 信心值。"""
    return (
        len(event.structured_detail or {}),
        len(event.detail or ""),
        event.confidence if event.confidence is not None else -1.0,
    )


def merge_events(primary: CanonicalEvent, other: CanonicalEvent) -> CanonicalEvent:
    """把兩筆同身分事件合併成一筆。

    以較完整者為基底，補齊對方獨有的結構化屬性，evidence 取聯集並保持穩定排序
    （排序穩定才能讓同一 snapshot 重跑產生完全相同的 item）。
    """
    base, extra = (
        (primary, other) if _completeness(primary) >= _completeness(other) else (other, primary)
    )

    structured = dict(extra.structured_detail or {})
    structured.update(base.structured_detail or {})

    evidence = tuple(sorted(set(base.evidence_conversation_ids) | set(extra.evidence_conversation_ids)))

    confidences = [
        value for value in (base.confidence, extra.confidence) if value is not None
    ]
    return replace(
        base,
        structured_detail=structured,
        evidence_conversation_ids=evidence,
        confidence=max(confidences) if confidences else None,
        # 初建來源固定為較早的 chunk，避免重跑時因合併順序改變而漂移
        source_chunk_id=min(
            filter(None, (base.source_chunk_id, extra.source_chunk_id)), default=base.source_chunk_id
        ),
        conversation_id=base.conversation_id or extra.conversation_id,
    )


def deduplicate(
    events: Sequence[CanonicalEvent],
    *,
    slot_minutes: int,
    lexicon=None,
) -> tuple[tuple[CanonicalEvent, ...], DedupStats]:
    """對同一 session 的事件做兩層去重。"""
    if not events:
        return (), DedupStats()

    # 第一層：canonical key 完全相同
    by_key: dict[str, CanonicalEvent] = {}
    key_merged = 0
    for event in events:
        existing = by_key.get(event.canonical_event_key)
        if existing is None:
            by_key[event.canonical_event_key] = event
        else:
            by_key[event.canonical_event_key] = merge_events(existing, event)
            key_merged += 1

    # 第二層：同 slot／subject／concept 但謂語漂移
    alias_merged = 0
    grouped: dict[tuple[str, str, str], list[CanonicalEvent]] = {}
    for event in by_key.values():
        date_part, slot_part, *_ = event.canonical_event_key.split("#")
        grouped.setdefault((f"{date_part}#{slot_part}", event.subject, event.concept_id), []).append(
            event
        )

    merged: list[CanonicalEvent] = []
    for (slot_key, subject, concept_id), group in grouped.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        target_predicate = _preferred_predicate(group, concept_id, lexicon)
        logger.info(
            "謂語漂移合併：slot=%s subject=%s concept_id=%s predicates=%s -> %s",
            slot_key,
            subject,
            concept_id,
            [event.predicate for event in group],
            target_predicate,
        )

        combined = group[0]
        for event in group[1:]:
            combined = merge_events(combined, event)
            alias_merged += 1
        merged.append(_rekey(combined, target_predicate, slot_minutes))

    ordered = tuple(sorted(merged, key=lambda event: (event.ts, event.event_id)))
    return ordered, DedupStats(
        input_count=len(events),
        output_count=len(ordered),
        key_merged=key_merged,
        alias_merged=alias_merged,
    )


def _preferred_predicate(
    group: Sequence[CanonicalEvent],
    concept_id: str,
    lexicon,
) -> str:
    """挑出合併後要用的謂語。

    優先取 lexicon 對該 concept 登記的 canonical 值（依 lexicon 順序，保證確定性）；
    都沒登記時取字典序最小者——理由只是要一個穩定規則，不是語意上更好。
    """
    predicates = {event.predicate for event in group}
    if lexicon is not None:
        for candidate in lexicon.candidates(concept_id):
            if candidate in predicates:
                return candidate
    return sorted(predicates)[0]


def _rekey(event: CanonicalEvent, predicate: str, slot_minutes: int) -> CanonicalEvent:
    """換謂語後重算 canonical key 與 event_id。"""
    if event.predicate == predicate:
        return event
    new_key = canonical_event_key(event.ts, event.subject, predicate, slot_minutes)
    return replace(
        event,
        predicate=predicate,
        canonical_event_key=new_key,
        event_id=event_id_for(event.elder_id, new_key),
    )
