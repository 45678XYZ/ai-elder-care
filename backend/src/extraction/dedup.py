"""Batch 記憶體內 Slot 去重模組。規範見 docs/framework.md 的「Batch 記憶體內去重」。

去重機制與階段流程：
1. 第一階段（精確 Key 去重）：
   - 對同一 session 內的事件按 `canonical_event_key`（`Date#Slot#Subject#Predicate`）收斂
   - 相同 Key 之事件透過 `merge_events` 合併，保留屬性最完整者，並將對話證據（evidence）取聯集

2. 第二階段（語義相似度去重 embedding similarity fallback）：
   - 針對同 Slot、同 Subject、同 `concept_id` 但謂語文字不同的事件進行二次收斂
   - 透過 embedding cosine similarity（門檻 MERGE_SIM_THRESHOLD = 0.75）判定語義等價
   - 取代舊版的 alias-based 去重，支援開放世界謂語 (Open-World Predicate)

3. 確定性與冪等保證：
   - 排序與選擇邏輯皆具確定性，確保相同 snapshot 重複執行產生完全一致的收斂結果
"""

from collections.abc import Sequence
from dataclasses import replace
import logging

import numpy as np

from .canonical import canonical_event_key, event_id_for
from .models import CanonicalEvent, DedupStats

logger = logging.getLogger(__name__)

# 語義相似度合併門檻：cosine similarity >= 此值視為同一事件的謂語漂移
MERGE_SIM_THRESHOLD = 0.75


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
    embedder=None,
) -> tuple[tuple[CanonicalEvent, ...], DedupStats]:
    """對單一 session 萃出的事件列表執行兩階段去重與收斂。

    Args:
        embedder: 具有 `embed(text) -> np.ndarray` 方法的 embedding provider，
                  用於第二階段的語義相似度去重。若為 None 則跳過語義去重。

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

    # 第二階段：同 slot／subject／concept 但謂語文字不同者，以 embedding similarity 判定是否合併
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

        if embedder is not None:
            # 使用 embedding cosine similarity 進行語義去重
            merge_result, merge_count = _embedding_merge_group(group, embedder, slot_minutes)
            merged.extend(merge_result)
            alias_merged += merge_count
        else:
            # 無 embedder 時回退：保留全部事件不做語義合併
            merged.extend(group)

    ordered = tuple(sorted(merged, key=lambda event: (event.ts, event.event_id)))
    return ordered, DedupStats(
        input_count=len(events),
        output_count=len(ordered),
        key_merged=key_merged,
        alias_merged=alias_merged,
    )


def _get_embed_vector(embedder, text: str) -> np.ndarray:
    """軟性適配各式 Embedder 介面 (embed / embed_query / embed_documents)。"""
    if hasattr(embedder, "embed"):
        return np.array(embedder.embed(text), dtype=np.float32)
    if hasattr(embedder, "embed_query"):
        return np.array(embedder.embed_query(text), dtype=np.float32)
    if hasattr(embedder, "embed_documents"):
        return np.array(embedder.embed_documents([text])[0], dtype=np.float32)
    raise AttributeError("Embedder 缺乏相容的向量介面")


def _embedding_merge_group(
    group: list[CanonicalEvent],
    embedder,
    slot_minutes: int,
) -> tuple[list[CanonicalEvent], int]:
    """在同 (slot, subject, concept_id) 群組內，以 embedding cosine similarity 合併語義相近的事件。

    使用貪婪聚類 (greedy clustering) 策略：
    1. 按資料完整度由高至低排序（最完整者作為各聚類的代表）
    2. 每個事件與現有聚類代表計算 cosine similarity
    3. 若 >= MERGE_SIM_THRESHOLD 則合併至該聚類
    4. 否則開啟新聚類

    運算成本：每個 predicate 的 embedding 約 1-5ms（本地 sentence-transformers），
    對 n 個事件的群組（通常 n <= 5），pairwise 比較完全可以忽略。
    """
    # 按完整度排序，最完整者優先成為聚類代表
    sorted_group = sorted(group, key=_completeness, reverse=True)

    # 計算所有 predicate 的 embedding 向量
    pred_embeddings: dict[str, np.ndarray] = {}
    for event in sorted_group:
        pred = event.predicate
        if pred not in pred_embeddings:
            try:
                vec = _get_embed_vector(embedder, pred)
                norm = np.linalg.norm(vec)
                pred_embeddings[pred] = vec / (norm + 1e-9) if norm > 0 else vec
            except Exception:
                pred_embeddings[pred] = np.zeros(1)

    # 貪婪聚類
    clusters: list[CanonicalEvent] = []
    cluster_preds: list[str] = []
    merge_count = 0

    for event in sorted_group:
        event_vec = pred_embeddings.get(event.predicate)
        if event_vec is None or event_vec.size <= 1:
            clusters.append(event)
            cluster_preds.append(event.predicate)
            continue

        best_idx = -1
        best_sim = -1.0
        for idx, rep_pred in enumerate(cluster_preds):
            rep_vec = pred_embeddings.get(rep_pred)
            if rep_vec is None or rep_vec.size <= 1:
                continue
            sim = float(np.dot(event_vec, rep_vec))
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_sim >= MERGE_SIM_THRESHOLD and best_idx >= 0:
            logger.info(
                "語義去重合併：'%s' ↔ '%s' (sim=%.3f, concept=%s)",
                event.predicate,
                cluster_preds[best_idx],
                best_sim,
                event.concept_id,
            )
            clusters[best_idx] = merge_events(clusters[best_idx], event)
            merge_count += 1
        else:
            clusters.append(event)
            cluster_preds.append(event.predicate)

    return clusters, merge_count


