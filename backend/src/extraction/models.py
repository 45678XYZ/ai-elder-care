"""萃取 pipeline 的內部資料模型。

這些型別只在 pipeline 內部流動，不是 API 契約也不是 DynamoDB 欄位；
對外欄位一律以 src/shared/models.py 與 docs/api.md 為準。
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LabelHit(BaseModel):
    """分類器命中的單一細分類節點。

    刻意不保留原文片段（決策 D：PII 最小化，不複製逐字稿）；
    需要追溯原文時由 `evidence_conversation_ids` 回 conversations 讀取。
    """

    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(description="細分類節點 concept_id")
    display_name: str = Field(default="", description="節點中文顯示名稱")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="分類信心值")


@dataclass(frozen=True)
class CandidateConcept:
    """檢索出的候選細分類節點，供分類器組 prompt 與收斂 enum。"""

    concept_id: str
    display_name: str
    definition: str
    retrieval_description: str
    synonyms: tuple[str, ...] = ()
    similarity: float = 0.0


@dataclass(frozen=True)
class ClassificationResult:
    """分類器輸出。

    `rationale` 只用於觀測與除錯，不落地到 events（決策 D：不複製逐字稿、PII 最小化）。
    """

    chunk_id: str
    hits: tuple[LabelHit, ...]
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedEvent:
    """萃取出的單一事件（尚未算 canonical key、尚未去重）。

    `attributes` 是通過驗證並剔除跨分類滲透後的結構化屬性，之後落到 `events.structured_detail`。
    """

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
class ExtractionResult:
    """單一 chunk 的萃取輸出。

    `dropped_events` 是驗證失敗後被丟棄的事件數（決策 I）；它是告警與品質觀測的訊號，
    不是錯誤——單一事件壞掉不該讓整個 chunk 變 failed。
    """

    chunk_id: str
    events: tuple[ExtractedEvent, ...]
    dropped_events: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class ComposedSchema:
    """動態 schema 組裝結果。

    同一份組裝結果同時提供兩種表示，確保 prompt 與驗證器不會走鐘：
    - `container_model`／`event_model`：後端驗證用的 Pydantic 模型
    - `schema_json` 與 `properties_by_concept`：組進 prompt 的規則描述
    """

    container_model: type[BaseModel]
    event_model: type[BaseModel]
    schema_json: dict[str, Any]
    concept_ids: tuple[str, ...]
    base_field_names: tuple[str, ...]
    global_property_names: tuple[str, ...]
    properties_by_concept: dict[str, tuple[str, ...]] = field(default_factory=dict)
    property_descriptions: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    def allowed_properties(self, concept_id: str) -> tuple[str, ...]:
        """該 concept 允許填寫的欄位全集（基底 + 全域 + 自身繼承鏈屬性）。"""
        return (
            self.base_field_names
            + self.global_property_names
            + self.properties_by_concept.get(concept_id, ())
        )
