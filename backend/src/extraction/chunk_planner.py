"""Chunk planner 與 `chunk_manifest`。

規範見 docs/framework.md 的「Static topic chunks」。要點：

- **core ranges 必須完整 partition 所有 turns 且互不重疊**，每個 turn 恰好被一個 chunk 當作
  core 處理一次；否則同一件事會被兩個 chunk 各萃取一次，或整段被漏掉。
- **context overlap 只供理解，不 emit 事件**。context-only turn 不會出現在 evidence，
  也不會產生事件。
- **`chunk_id` 由 `session_snapshot_hash + 首尾 core turn id + ordinal` 穩定產生**，
  與模型、時間、執行次數無關。
- **manifest 首次成功後條件式持久化**，所有 retry、duplicate delivery 與 DLQ replay 重用
  同一份；這是「分塊可以非確定性」與「batch 必須冪等」兩個要求能並存的原因。
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

# 前後各帶幾個 turn 當理解脈絡；只讀不 emit
DEFAULT_CONTEXT_OVERLAP = 1


class ChunkPlanError(ValueError):
    """manifest 不合法。"""


@dataclass(frozen=True)
class PlannedChunk:
    """單一 chunk 的靜態處理範圍。

    索引都是 session 內 frozen turns 的位置，`core_*` 為含頭含尾的閉區間。
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
        """壓成 session item 內的 compact metadata（不含逐字稿）。"""
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
    """一個 closed session 的完整 chunk 規劃。"""

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
    """穩定的 chunk ID。

    刻意不含模型版本、時間或標籤：同一份 frozen snapshot 重跑時 chunk ID 必須相同，
    否則 retry 會產生新的 `source_chunk_id`，讓稽核追不回來源。
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
    """由邊界產生 manifest。"""
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
    """檢查 core ranges 恰好覆蓋每個 turn 一次。"""
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
    """由持久化的 manifest 還原。

    retry、duplicate delivery 與 DLQ replay 都走這條路徑重用首次規劃，不重新分塊。
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
    """組 chunk 的逐字稿。

    context-only 的 turn 標上前綴，讓模型知道那是脈絡而非本塊要萃取的內容；
    這些 turn 不會出現在 evidence，也不該產生事件。
    """
    lines: list[str] = []
    for index in range(chunk.context_start, chunk.context_end + 1):
        turn = turns[index]
        prefix = "" if chunk.core_start <= index <= chunk.core_end else "（脈絡）"
        lines.append(f"{prefix}{turn.speaker}：{turn.text}")
    return "\n".join(lines)


def core_turn_ids(turns: Sequence[Turn], chunk: PlannedChunk) -> tuple[str, ...]:
    """chunk 的 core turn IDs；evidence 只能取自這裡。"""
    return tuple(
        turns[index].conversation_id for index in range(chunk.core_start, chunk.core_end + 1)
    )


def reference_datetime_for(turns: Sequence[Turn], chunk: PlannedChunk) -> str:
    """時序推導的參考時間：core range 最後一個 turn 的 `created_at`。

    不可用當下時間——retry 或 DLQ replay 會落在不同時刻，Slot 與 canonical key 就會漂移。
    取最後一個 turn 是因為相對時間表達（「剛剛」「昨天」）通常相對於說話當下。
    """
    return turns[chunk.core_end].created_at
