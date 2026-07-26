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
