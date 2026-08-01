"""萃取 pipeline 的內部資料模型。

這些型別只在 pipeline 內部流動，不是 API 契約也不是 DynamoDB 欄位；
對外欄位一律以 src/shared/models.py 與 docs/api.md 為準。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Turn:
    """單一對話輪次資料容器。"""

    conversation_id: str
    speaker: str
    text: str
    created_at: str


@dataclass(frozen=True)
class ExtractedEvent:
    """萃取出的單一事件（尚未算 canonical key、尚未去重）。"""

    concept_id: str
    subject: str
    predicate: str
    summary: str
    attributes: dict[str, Any] = field(default_factory=dict)
    raw_temporal_expression: str | None = None
    observed_at: str | None = None
    confidence: float | None = None
    event_index: int = 0


@dataclass(frozen=True)
class CanonicalEvent:
    """已算出 canonical 身分、可直接寫入 events 表的事件。"""

    elder_id: str
    event_id: str
    canonical_event_key: str
    ts: str
    type: str
    concept_id: str
    taxonomy_version: str
    subject: str
    predicate: str
    detail: str
    structured_detail: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    session_id: str | None = None
    source_chunk_id: str | None = None
    conversation_id: str | None = None
    evidence_conversation_ids: tuple[str, ...] = ()
    extraction_track: str = "batch"
    source: str = "conversation"

    def to_event_item(self) -> dict[str, Any]:
        """轉成 `shared.db.create_event` 的輸入。"""
        return {
            "elder_id": self.elder_id,
            "event_id": self.event_id,
            "canonical_event_key": self.canonical_event_key,
            "ts": self.ts,
            "type": self.type,
            "concept_id": self.concept_id,
            "taxonomy_version": self.taxonomy_version,
            "detail": self.detail,
            "structured_detail": self.structured_detail or None,
            "confidence": self.confidence,
            "session_id": self.session_id,
            "source_chunk_id": self.source_chunk_id,
            "conversation_id": self.conversation_id,
            "evidence_conversation_ids": list(self.evidence_conversation_ids),
            "extraction_track": self.extraction_track,
            "source": self.source,
        }


@dataclass(frozen=True)
class DedupStats:
    """去重統計；合併率是觀測 canonical key 設計是否有效的主要訊號。"""

    input_count: int = 0
    output_count: int = 0
    key_merged: int = 0
    alias_merged: int = 0

    @property
    def merge_rate(self) -> float:
        if self.input_count == 0:
            return 0.0
        return (self.input_count - self.output_count) / self.input_count
