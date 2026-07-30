"""Batch 記憶體內 Slot 去重模組。規範見 docs/framework.md 的「Batch 記憶體內去重」。

去重機制與階段流程：
1. 第一階段（精確 Key 去重）：
   - 對同一 session 內的事件按 `canonical_event_key`（`Date#Slot#Subject#Predicate`）收斂
   - 相同 Key 之事件透過 `merge_events` 合併，保留屬性最完整者，並將對話證據（evidence）取聯集

2. 第二階段（別名同義詞去重 alias fallback）：
   - 針對同 Slot、同 Subject、同 `concept_id` 但謂語文字漂移的事件進行二次收斂
   - 透過 `lexicon` 受控詞彙檔選擇規範謂語，解決 LLM 萃取時近義詞漂移問題
   - 合併後重算 `canonical_event_key` 與 `event_id`

3. 確定性與冪等保證：
   - 排序與選擇邏輯皆具確定性，確保相同 snapshot 重複執行產生完全一致的收斂結果
"""

from collections.abc import Sequence
from dataclasses import replace
import logging

from .canonical import canonical_event_key, event_id_for
from .models import CanonicalEvent, DedupStats

logger = logging.getLogger(__name__)


def _completeness(event: CanonicalEvent) -> tuple[int, int, float]:
    """計算事件資料完整度權重，做為合併時選擇基底事件（base）的比較依據。"""
    return (
        len(event.structured_detail or {}),
        len(event.detail or ""),
        event.confidence if event.confidence is not None else -1.0,
    )


def merge_events(primary: CanonicalEvent, other: CanonicalEvent) -> CanonicalEvent:
    """合併兩筆同身分（同 Key 或同義）的 CanonicalEvent 實體。

    選擇資訊最完整者為基底，合併補齊獨有結構欄位（structured_detail）、將對話證據（evidence）取聯集並以穩定排序保存，
    維持重跑時輸出一致性。
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
        # 初建來源固定取較早的 chunk ID，避免重複執行時因處理順序影響結果
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
    """對單一 session 萃出的事件列表執行兩階段去重與收斂。

    Returns:
        (已去重且排序穩定的事件 tuple, 去重統計指標 DedupStats)
    """
    if not events:
        return (), DedupStats()

    # 第一階段：按 canonical_event_key 完全相同者進行無失真收斂
    by_key: dict[str, CanonicalEvent] = {}
    key_merged = 0
    for event in events:
        existing = by_key.get(event.canonical_event_key)
        if existing is None:
            by_key[event.canonical_event_key] = event
        else:
            by_key[event.canonical_event_key] = merge_events(existing, event)
            key_merged += 1

    # 第二層：同 slot／subject／concept 且謂語為別名漂移者才合併
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

        # 依據受控詞彙別名拆分子群組（例如 "測血壓" 與 "量血壓" 屬同一個別名群組）
        subgroups: dict[str, list[CanonicalEvent]] = {}
        for event in group:
            target_pred = event.predicate
            if lexicon is not None:
                res = lexicon.concepts.get(concept_id)
                if res:
                    target_pred = res.aliases.get(event.predicate, event.predicate)
            subgroups.setdefault(target_pred, []).append(event)

        # 若子群組數量多於 1 個（如包含通用與具體謂語），但皆無相互衝突的結構化數據，則進行漂移收斂
        if len(subgroups) > 1:
            # 檢查是否有具體相異的獨立測量 (例如量血壓 vs 量體重)
            has_distinct_measurements = any(
                len(e.structured_detail or {}) > 0 for e in group
            ) and len({e.predicate for e in group if e.predicate in (lexicon.candidates(concept_id) if lexicon else ())}) > 1

            if not has_distinct_measurements:
                # 屬同概念內的謂語漂移，依 preferred_predicate 進行收斂
                target_pred = _preferred_predicate(group, concept_id, lexicon)
                combined = group[0]
                for event in group[1:]:
                    combined = merge_events(combined, event)
                    alias_merged += 1
                merged.append(_rekey(combined, target_pred, slot_minutes))
                continue

        for pred_key, sub_events in subgroups.items():
            if len(sub_events) == 1:
                merged.append(_rekey(sub_events[0], pred_key, slot_minutes))
                continue

            target_pred = _preferred_predicate(sub_events, concept_id, lexicon)
            combined = sub_events[0]
            for event in sub_events[1:]:
                combined = merge_events(combined, event)
                alias_merged += 1
            merged.append(_rekey(combined, target_pred, slot_minutes))

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
    """在同義謂語群組中挑選規範化（canonical）謂語。

    優先挑選受控詞彙集（lexicon）登記之規範值；若皆未登記則取字典序最小者，以建立確定性的全序選擇規則。
    """
    predicates = {event.predicate for event in group}
    if lexicon is not None:
        for candidate in lexicon.candidates(concept_id):
            if candidate in predicates:
                return candidate
    return sorted(predicates)[0]


def _rekey(event: CanonicalEvent, predicate: str, slot_minutes: int) -> CanonicalEvent:
    """變更事件之謂語，並依據新謂語重新計算對應之 canonical_event_key 與 event_id。"""
    if event.predicate == predicate:
        return event
    new_key = canonical_event_key(event.ts, event.subject, predicate, slot_minutes)
    return replace(
        event,
        predicate=predicate,
        canonical_event_key=new_key,
        event_id=event_id_for(event.elder_id, new_key),
    )

