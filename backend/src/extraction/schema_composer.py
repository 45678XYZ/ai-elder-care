"""動態 Schema 組裝模組（Universal Ancestral Traversal）。

提供對對話片段 (chunk) 經過 HMLC 剪枝後的命中標籤，動態走訪本體論祖先樹並組裝
專屬的 Pydantic 模型與 Prompt 規則。
架構規範與設計決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

本模組設計目的與核心機制：
- **動態祖先走訪 (Universal Ancestral Traversal)**：對每個命中節點往上走到根，沿途收集各層級的專屬屬性欄位，為該次萃取打造最輕量、無無關欄位干擾的專屬 Pydantic 驗證器。
- **單一源頭雙向產出（決策 H）**：由同一份 `ComposedSchema` 組裝結果同時產出 Prompt 規則文字 (`describe_for_prompt`) 與 Pydantic 驗證模型，確保提示詞白名單與後續驗證邏輯 100% 同步不走鐘。
- **PII 最小化隔離（決策 D）**：主動於 Schema 中排除原文片段欄位 (`source_utterance`)，從根源封鎖逐字稿複製至寫入層。
- **基底身分欄位**：包含 `subject` 與 `predicate`，為後續時間軸正規化與 Canonical Event Key 計算奠定基礎。
- **屬性跨概念防滲透 (`prune_irrelevant_event_properties`)**：單一容器 Schema 同時載入多概念屬性時，於後處理強制剔除未列於該概念白名單的屬性。
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
import hashlib
import json
import logging

from pydantic import BaseModel, ConfigDict, Field, create_model

from .models import ComposedSchema, LabelHit
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 所有事件共用的固定基底欄位，奠定事件基礎身分與 Canonical Key (`elder_id + Date + Slot + Subject + Predicate`)
BASE_EVENT_FIELDS: tuple[str, ...] = (
    "event_index",
    "concept_id",
    "subject",
    "predicate",
    "event_summary",
    "raw_temporal_expression",
    "observed_at",
)

# 遵循決策 D：將逐字稿原文欄位排除於 Schema 之外，從模型輸入端即防止 PII 敏感對話複製至寫入層
EXCLUDED_GLOBAL_PROPERTIES: frozenset[str] = frozenset({"source_utterance"})

# 屬性註冊表型別映射至 Python 型別
_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "float": float,
    "integer": int,
    "boolean": bool,
    "datetime": str,
    "enum": str,
    "array": list[str],
    "list": list[str],
    # 開放式 object 無法落在 Bedrock 的 schema 子集內（additionalProperties 只能是 false），
    # 故以字串承載；structured_detail 本身是 Map，內層存字串不影響下游使用
    "object": str,
}

# Bedrock structured outputs 不支援的 JSON Schema 限制關鍵字
_UNSUPPORTED_SCHEMA_KEYWORDS: frozenset[str] = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "maxItems",
        "uniqueItems",
        "patternProperties",
    }
)


def _ancestor_chain(taxonomy: Taxonomy, concept_id: str) -> tuple[str, ...]:
    """取得自根節點以下至命中節點的祖先路徑（排除 level 0 的根節點 `UCO`）。

    排除根節點可避免無屬性的無效走訪，順序由淺至深方便順序繼承屬性定義。
    """
    chain = [concept_id, *taxonomy.ancestors(concept_id)]
    ordered = tuple(reversed([cid for cid in chain if taxonomy.get(cid) is not None]))
    root = ordered[0] if ordered else None
    if root is not None and (taxonomy.nodes[root].level == 0):
        return ordered[1:]
    return ordered


def _node_property_definitions(taxonomy: Taxonomy, concept_id: str) -> list[dict[str, Any]]:
    """讀取指定節點在屬性註冊表中定義的專屬屬性清單。"""
    node_properties = taxonomy.property_registry.get("node_properties") or {}
    props = node_properties.get(concept_id)
    return list(props) if isinstance(props, list) else []


def _field_spec(prop: dict[str, Any], *, allow_required: bool) -> tuple[Any, Any]:
    """由屬性定義生成 Pydantic `(型別, Field)` 規格對。

    全域屬性（`allow_required=True`）適用於所有事件，當 registry 標示為非空時可設為必填；
    而節點專屬屬性一律必須可為 `None`（`allow_required=False`），因為動態容器 Schema
    會同時包含多個概念的屬性，若將某一概念的專屬屬性設為必填，會導致其他概念的事件驗證失敗。
    """
    py_type = _TYPE_MAP.get(prop.get("type", "string"), str)
    description = prop.get("description", "")
    if allow_required and not prop.get("nullable", True):
        return (py_type, Field(description=description))
    return (py_type | None, Field(default=None, description=description))


def compose_multi_event(
    hits: Sequence[LabelHit],
    taxonomy: Taxonomy,
) -> ComposedSchema:
    """為單次 Single-Pass 萃取組裝可一次回應多筆事件的動態容器 Schema。

    透過祖先鏈走訪收集所有相關屬性，並以 `Literal[concept_ids]` 將 `concept_id` 收斂在剪枝後的標籤內，
    防止 LLM 輸出未授權的細分類節點。
    """
    concept_ids = tuple(hit.concept_id for hit in hits if taxonomy.get(hit.concept_id) is not None)
    if not concept_ids:
        raise ValueError("組裝 schema 至少需要一個存在於分類體系的 concept_id")

    # concept_id 以 Literal 收斂，直接在 Pydantic 驗證層擋掉幻覺標籤；順序沿用剪枝後的確定性順序
    concept_literal = Literal[concept_ids]  # type: ignore[valid-type]

    event_fields: dict[str, Any] = {
        "event_index": (int, Field(description="事件在本 chunk 內的序號，從 0 開始")),
        "concept_id": (concept_literal, Field(description="事件的細分類節點，只能從允許清單中選")),
        "subject": (str, Field(description="事件主體，如「長者」或具名親友")),
        "predicate": (str, Field(description="單一語意謂語，如「服用血壓藥」「公園散步」")),
        "event_summary": (str, Field(description="事件的自然語言精簡描述，不要抄寫整段對話")),
        "raw_temporal_expression": (
            str | None,
            Field(default=None, description="原始時間表達，如「昨天晚上」「早上」"),
        ),
        "observed_at": (
            str | None,
            Field(default=None, description="依 reference_datetime 推得的 ISO 8601 絕對時間"),
        ),
    }

    global_names: list[str] = []
    for prop in taxonomy.property_registry.get("global_properties") or []:
        name = prop.get("name")
        if not name or name in EXCLUDED_GLOBAL_PROPERTIES or name in event_fields:
            continue
        event_fields[name] = _field_spec(prop, allow_required=True)
        global_names.append(name)

    properties_by_concept: dict[str, tuple[str, ...]] = {}
    property_descriptions: dict[str, str] = {}

    for concept_id in concept_ids:
        own: list[str] = []
        for node_id in _ancestor_chain(taxonomy, concept_id):
            for prop in _node_property_definitions(taxonomy, node_id):
                name = prop.get("name")
                if not name:
                    continue
                own.append(name)
                property_descriptions.setdefault(name, prop.get("description", ""))
                if name not in event_fields:
                    event_fields[name] = _field_spec(prop, allow_required=False)
        # 同一屬性可能在鏈上重複出現，去重但保持出現順序
        properties_by_concept[concept_id] = tuple(dict.fromkeys(own))

    event_model = create_model(
        "CareEventItem",
        __config__=ConfigDict(extra="forbid"),
        **event_fields,
    )
    container_model = create_model(
        "MultiEventExtraction",
        __config__=ConfigDict(extra="forbid"),
        chunk_id=(str, Field(description="對話塊識別碼，必須與輸入一致")),
        reference_datetime=(str, Field(description="時間對齊的參考基準（ISO 8601，含 +08:00）")),
        events=(list[event_model], Field(default_factory=list, description="拆分出的獨立事件清單")),  # type: ignore[valid-type]
    )

    schema_json = container_model.model_json_schema()

    return ComposedSchema(
        container_model=container_model,
        event_model=event_model,
        schema_json=schema_json,
        concept_ids=concept_ids,
        base_field_names=BASE_EVENT_FIELDS,
        global_property_names=tuple(global_names),
        properties_by_concept=properties_by_concept,
        property_descriptions=property_descriptions,
        fingerprint=schema_fingerprint(concept_ids, tuple(event_fields)),
    )


def schema_fingerprint(concept_ids: Sequence[str], field_names: Sequence[str]) -> str:
    """計算動態 Schema 形狀的穩定雜湊指紋。

    動態 Schema 每換一組標籤就是一份新語法，此指紋用於監控 Bedrock 靜態語法快取命中率，
    同時提供單元測試進行 Schema 形狀的斷言鎖定。
    """
    payload = json.dumps(
        {"concepts": sorted(concept_ids), "fields": sorted(field_names)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def prune_irrelevant_event_properties(
    event: Mapping[str, Any],
    composed: ComposedSchema,
) -> dict[str, Any]:
    """強制剔除不屬於該事件概念 (concept_id) 的跨概念滲透屬性。

    在多事件容器 Schema 中，LLM 偶爾會誤將某概念的屬性（如「收縮壓」）填入另一概念的事件（如「睡眠事件」）。
    本函式採白名單嚴格過濾：非該概念白名單內的屬性一律剔除，若被剔除之屬性含有非 None 值則發出警告，
    確保錯誤屬性不被無聲寫入 DynamoDB `structured_detail`。
    """
    concept_id = event.get("concept_id")
    if not concept_id:
        return dict(event)

    allowed = set(composed.allowed_properties(str(concept_id)))
    cleaned: dict[str, Any] = {}
    for key, value in event.items():
        if key in allowed:
            cleaned[key] = value
        elif value is not None:
            logger.warning(
                "剔除跨分類滲透的屬性：concept_id=%s property=%s",
                concept_id,
                key,
            )
    return cleaned


def describe_for_prompt(
    composed: ComposedSchema,
    taxonomy: Taxonomy,
    *,
    predicate_candidates: Mapping[str, Sequence[str]] | None = None,
    other_predicate_token: str = "__other__",
) -> str:
    """將組裝好的 Schema 轉譯為 Prompt 中的規則描述區塊。

    由於單次 Single-Pass 萃取不依賴硬約束解碼，Prompt 必須明確指出允許的分類節點、
    各節點的專屬屬性白名單與全空填 null 規則，傳遞最完整的上下文引導 LLM。
    predicate 欄位採開放世界策略，由 LLM 自行撰寫精簡動作短語，後續去重由 embedding similarity 處理。
    """
    lines: list[str] = []

    lines.append("## 允許的分類節點（concept_id 只能從下列選項中挑一個）")
    for concept_id in composed.concept_ids:
        node = taxonomy.get(concept_id)
        display = node.display_name if node else concept_id
        definition = (node.definition if node else "") or ""
        lines.append(f"- `{concept_id}`（{display}）：{definition}")

    lines.append("")
    lines.append("## 通用欄位（每筆事件都要判斷是否填寫）")
    for name in composed.base_field_names:
        lines.append(f"- `{name}`")
    for name in composed.global_property_names:
        description = composed.property_descriptions.get(name, "")
        lines.append(f"- `{name}`：{description}" if description else f"- `{name}`")

    lines.append("")
    lines.append("## 各分類節點專屬屬性白名單（屬性隔離）")
    lines.append("只有列在該節點下的屬性可以填寫；不得把某節點的屬性填到其他節點的事件上。")
    for concept_id in composed.concept_ids:
        props = composed.properties_by_concept.get(concept_id, ())
        rendered = "、".join(f"`{name}`" for name in props) if props else "（無專屬屬性）"
        lines.append(f"- `{concept_id}`：{rendered}")

    lines.append("")
    lines.append("## predicate 填寫規則")
    lines.append(
        "predicate 請用精簡的動詞短語描述這個事件的核心行為（如「吃早餐」「膝蓋發出聲響」「服用糖尿病藥」「幫鄰居澆花」）。"
        "不要填抽象的類別名稱，不要複製 concept_id。"
    )

    lines.append("")
    lines.append("## 輸出規則")
    lines.append("1. 只輸出符合下方 JSON Schema 的 JSON，不要加說明文字或程式碼註解。")
    lines.append("2. 未在對話中提及的欄位一律填 `null`，不要推測、不要沿用其他事件的值。")
    lines.append("3. 同一件事只輸出一筆事件；不同主體或不同謂語則拆成多筆。")
    lines.append("4. `event_summary` 必須描述已發生的事實結果，禁止使用「提到」「詢問」「被建議」「準備」「考慮」等過程性描述。")
    lines.append("5. `subject` 與 `predicate` 必填，兩者決定事件身分。")
    lines.append("6. `confidence_score` 必填，填 0.0–1.0 之間的浮點數。")

    lines.append("")
    lines.append("## JSON Schema")
    lines.append("```json")
    lines.append(json.dumps(composed.schema_json, ensure_ascii=False, indent=2, sort_keys=True))
    lines.append("```")

    return "\n".join(lines)



def check_schema_constraints(schema: Mapping[str, Any]) -> list[str]:
    """檢查生成的 JSON Schema 是否符合 Bedrock structured outputs 支援的子集規範。

    傳回違規描述列表；傳回空列表代表該 Schema 可安全切換至 Bedrock 網關硬約束模式。
    """
    violations: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
            return
        if not isinstance(node, Mapping):
            return

        for keyword in _UNSUPPORTED_SCHEMA_KEYWORDS:
            if keyword in node:
                violations.append(f"{path}: 不支援的關鍵字 {keyword}")

        if "minItems" in node and node["minItems"] not in (0, 1):
            violations.append(f"{path}: minItems 只能是 0 或 1，實際為 {node['minItems']}")

        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                violations.append(f"{path}: object 避免未約束屬性，必須設定 additionalProperties=false")

        for key, value in node.items():
            walk(value, f"{path}.{key}")

    walk(schema, "$")
    return violations

