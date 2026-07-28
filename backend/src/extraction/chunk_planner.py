"""Chunk Planner 與 `chunk_manifest` 管理模組。規範見 docs/framework.md 的「Static topic chunks」。

核心設計原則與約束：
1. Core Range 完整切割：
   - core ranges 必須完全覆蓋 session 內所有 turns 且互不重疊
   - 確保每個 turn 恰好作為 core 被處理一次，防止漏萃取或跨 chunk 重複萃取

2. Context Overlap 邊界：
   - context overlap 僅作為 LLM 理解前文脈絡（只讀不 emit）
   - context-only turns 嚴禁產生事件，亦不得作為對話證據（evidence_conversation_ids）

3. 確定性 Chunk ID 派生：
   - `chunk_id` 由 `session_snapshot_hash + 首尾 core turn id + ordinal` 雜湊產生
   - 刻意排除執行時間與模型版本，保證同一 snapshot 重跑時產生完全一致的 ID，維護稽核追溯

4. Manifest 條件式持久化：
   - 首次劃分後寫入 DynamoDB 持久化；重試（retry）、重複交付（duplicate delivery）與 DLQ 重播均重用現成 manifest，達到最終冪等
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
import hashlib
import logging

from .chunker import Turn

logger = logging.getLogger(__name__)

CHUNK_ID_PREFIX = "chk_"
CHUNK_ID_HASH_LENGTH = 12

# 上下文脈絡包含的前後 turn 數量；僅供 LLM 理解語意脈絡，不得作為事件萃取來源
DEFAULT_CONTEXT_OVERLAP = 1


class ChunkPlanError(ValueError):
    """Manifest 格式不合法或未滿足劃分條件約束。"""


@dataclass(frozen=True)
class PlannedChunk:
    """單一 Chunk 的靜態處理範圍與索引。

    索引均為 session 內 frozen turns 的 0-indexed 位置，`core_*` 包含頭尾端點（閉區間）。
    """

    chunk_id: str
    ordinal: int
    core_start: int
    core_end: int
    context_start: int
    context_end: int
    first_core_turn_id: str
    last_core_turn_id: str

    @property
    def core_turn_count(self) -> int:
        return self.core_end - self.core_start + 1

    def to_manifest_entry(self) -> dict[str, Any]:
        """壓縮為 session item 內部儲存的 compact metadata 字典（不含逐字稿與原文）。"""
        return {
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "core_start": self.core_start,
            "core_end": self.core_end,
            "context_start": self.context_start,
            "context_end": self.context_end,
            "first_core_turn_id": self.first_core_turn_id,
            "last_core_turn_id": self.last_core_turn_id,
        }


@dataclass(frozen=True)
class ChunkManifest:
    """單一 closed session 之全量 Chunk 劃分與規劃物件。"""

    session_id: str
    session_snapshot_hash: str
    planner_version: str
    chunks: tuple[PlannedChunk, ...]
    strategy: str = ""
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_manifest(self) -> list[dict[str, Any]]:
        return [chunk.to_manifest_entry() for chunk in self.chunks]


def chunk_id_for(
    session_snapshot_hash: str,
    first_core_turn_id: str,
    last_core_turn_id: str,
    ordinal: int,
) -> str:
    """計算確定的 chunk ID。

    刻意排除模型版本與當下時間：保證同一 frozen snapshot 重跑時產生相同的 chunk ID，
    確保重試或重播放置時 source_chunk_id 能夠精準追溯歷史。
    """
    signature = f"{session_snapshot_hash}|{first_core_turn_id}|{last_core_turn_id}|{ordinal}"
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:CHUNK_ID_HASH_LENGTH]
    return f"{CHUNK_ID_PREFIX}{digest}"


def plan_chunks(
    session_id: str,
    session_snapshot_hash: str,
    turns: Sequence[Turn],
    boundaries: Sequence[int],
    *,
    planner_version: str,
    context_overlap: int = DEFAULT_CONTEXT_OVERLAP,
    strategy: str = "",
    fallback_used: bool = False,
) -> ChunkManifest:
    """根據語意邊界列表與全量 frozen turns 生成分塊規劃清單（ChunkManifest）。"""
    if not turns:
        raise ChunkPlanError("frozen turns 為空，無法規劃 chunk")
    if not boundaries or boundaries[0] != 0:
        raise ChunkPlanError("邊界必須以 0 開始")

    total = len(turns)
    starts = list(boundaries)
    chunks: list[PlannedChunk] = []
    for ordinal, start in enumerate(starts):
        end = (starts[ordinal + 1] - 1) if ordinal + 1 < len(starts) else total - 1
        if end < start:
            raise ChunkPlanError(f"邊界不遞增：ordinal={ordinal}")
        context_start = max(0, start - context_overlap)
        context_end = min(total - 1, end + context_overlap)
        chunks.append(
            PlannedChunk(
                chunk_id=chunk_id_for(
                    session_snapshot_hash,
                    turns[start].conversation_id,
                    turns[end].conversation_id,
                    ordinal,
                ),
                ordinal=ordinal,
                core_start=start,
                core_end=end,
                context_start=context_start,
                context_end=context_end,
                first_core_turn_id=turns[start].conversation_id,
                last_core_turn_id=turns[end].conversation_id,
            )
        )

    manifest = ChunkManifest(
        session_id=session_id,
        session_snapshot_hash=session_snapshot_hash,
        planner_version=planner_version,
        chunks=tuple(chunks),
        strategy=strategy,
        fallback_used=fallback_used,
    )
    validate_manifest(manifest, total)
    return manifest


def validate_manifest(manifest: ChunkManifest, total_turns: int) -> None:
    """驗證 Manifest 的 Core Ranges 是否滿足完整劃分且無重疊（Partition Constraint）。"""
    covered: list[int] = []
    for chunk in manifest.chunks:
        if chunk.core_start > chunk.core_end:
            raise ChunkPlanError(f"chunk {chunk.chunk_id} 的 core range 反向")
        covered.extend(range(chunk.core_start, chunk.core_end + 1))

    if sorted(covered) != list(range(total_turns)):
        raise ChunkPlanError(
            f"core ranges 未完整覆蓋所有 turn 或有重疊：covered={len(covered)} total={total_turns}"
        )

    ordinals = [chunk.ordinal for chunk in manifest.chunks]
    if ordinals != sorted(ordinals) or len(set(ordinals)) != len(ordinals):
        raise ChunkPlanError("chunk ordinal 必須唯一且遞增")

    chunk_ids = [chunk.chunk_id for chunk in manifest.chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ChunkPlanError("chunk_id 重複")


def manifest_from_entries(
    session_id: str,
    session_snapshot_hash: str,
    planner_version: str,
    entries: Sequence[dict[str, Any]],
) -> ChunkManifest:
    """從 DB 持久化之 compact entries 還原 ChunkManifest。

    重試（retry）、重複交付與 DLQ 重播皆直接還原重用，避免非確定性重新劃分。
    """
    chunks = tuple(
        PlannedChunk(
            chunk_id=entry["chunk_id"],
            ordinal=int(entry["ordinal"]),
            core_start=int(entry["core_start"]),
            core_end=int(entry["core_end"]),
            context_start=int(entry["context_start"]),
            context_end=int(entry["context_end"]),
            first_core_turn_id=entry["first_core_turn_id"],
            last_core_turn_id=entry["last_core_turn_id"],
        )
        for entry in sorted(entries, key=lambda item: int(item["ordinal"]))
    )
    return ChunkManifest(
        session_id=session_id,
        session_snapshot_hash=session_snapshot_hash,
        planner_version=planner_version,
        chunks=chunks,
    )


def render_chunk_text(turns: Sequence[Turn], chunk: PlannedChunk) -> str:
    """將指定 chunk 範圍內的 turns 組合為送給 LLM 的逐字稿文本。

    非 core 範圍的上下文加上 `（脈絡）` 前綴標記，提示 LLM 僅供參考不得萃取事件；
    脈絡範圍之 turn 不得包含於證據（evidence）集中。
    """
    lines: list[str] = []
    for index in range(chunk.context_start, chunk.context_end + 1):
        turn = turns[index]
        prefix = "" if chunk.core_start <= index <= chunk.core_end else "（脈絡）"
        lines.append(f"{prefix}{turn.speaker}：{turn.text}")
    return "\n".join(lines)


def core_turn_ids(turns: Sequence[Turn], chunk: PlannedChunk) -> tuple[str, ...]:
    """取得指定 chunk 的 core range turn IDs 集合。

    事件萃取之 evidence_conversation_ids 僅能從此集合挑選。
    """
    return tuple(
        turns[index].conversation_id for index in range(chunk.core_start, chunk.core_end + 1)
    )


def reference_datetime_for(turns: Sequence[Turn], chunk: PlannedChunk) -> str:
    """取得時序推導的基準時間戳記（取 core range 最末 turn 之 created_at）。

    嚴禁使用系統當下時間（datetime.now），否則重試或 DLQ 重播會導致 Slot 與 canonical key 發生時間漂移。
    使用最末 turn 時間戳記係因語意中相對時間表達（如「剛剛」、「剛才」）均相對於對話當下。
    """
    return turns[chunk.core_end].created_at

