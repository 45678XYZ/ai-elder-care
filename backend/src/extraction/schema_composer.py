"""動態 schema 組裝（Universal Ancestral Traversal）。

移植自 aws-hackathon 的 `dynamic_schema_composer`：對每個命中節點自身往上走到根，
沿途收集各層級的屬性欄位，組成該次萃取專用的 Pydantic 模型。

移植時的三項調整：

- **同一份組裝結果同時產出 prompt 表示與驗證器**（決策 H）。萃取階段不走 grammar 硬約束，
  所以「屬性隔離」不能只靠後處理清洗，必須在 prompt 裡明列每個 concept 的屬性白名單；
  兩者來自同一個組裝結果，不會走鐘。
- **不落地逐字稿欄位**（決策 D）。`source_utterance` 這類原文片段直接不進 schema，
  模型連填的機會都沒有，而不是寫進去再刪。
- **事件輸出新增 `subject`／`predicate`**。canonical key 由 `Date + Slot + Subject + Predicate`
  決定，沒有這兩個欄位就算不出事件身分。

輸出的 JSON Schema 刻意維持在 Bedrock structured outputs 支援的子集內
（`additionalProperties: false`、無數值與字串長度約束、無遞迴），
即使預設不啟用硬約束，也能靠 `EXTRACTION_MODE` 直接切換。
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

# 事件的固定基底欄位；所有 concept 共用
BASE_EVENT_FIELDS: tuple[str, ...] = (
    "event_index",
    "concept_id",
    "subject",
    "predicate",
    "event_summary",
    "raw_temporal_expression",
    "observed_at",
)

# 決策 D：原文片段不進 schema，避免逐字稿被複製到 events
EXCLUDED_GLOBAL_PROPERTIES: frozenset[str] = frozenset({"source_utterance"})

# registry 的型別字串 → Python 型別
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
    # 因此以字串承載；`structured_detail` 本身是 Map，內層存字串不影響下游使用
    "object": str,
}

# Bedrock structured outputs 不支援的 JSON Schema 關鍵字
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
    """自根之下到命中節點的路徑（不含根節點 `UCO`）。"""
    chain = [concept_id, *taxonomy.ancestors(concept_id)]
    ordered = tuple(reversed([cid for cid in chain if taxonomy.get(cid) is not None]))
    root = ordered[0] if ordered else None
    if root is not None and (taxonomy.nodes[root].level == 0):
        return ordered[1:]
    return ordered


def _node_property_definitions(taxonomy: Taxonomy, concept_id: str) -> list[dict[str, Any]]:
    node_properties = taxonomy.property_registry.get("node_properties") or {}
    props = node_properties.get(concept_id)
    return list(props) if isinstance(props, list) else []


def _field_spec(prop: dict[str, Any], *, allow_required: bool) -> tuple[Any, Any]:
    """由屬性定義產生 `(型別, Field)`。

    `allow_required` 只對全域屬性開放：全域屬性每筆事件都適用，registry 標 nullable=false
    時設為必填才有意義。節點專屬屬性一律可為 null——同一份 schema 會同時容納多個 concept
    的屬性，把某個 concept 的屬性設成必填會讓其他 concept 的事件永遠驗證失敗。
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
    """為 single-pass 萃取組裝「一次回應多筆事件」的容器 schema。"""
    concept_ids = tuple(hit.concept_id for hit in hits if taxonomy.get(hit.concept_id) is not None)
    if not concept_ids:
        raise ValueError("組裝 schema 至少需要一個存在於分類體系的 concept_id")

    # concept_id 以 Literal 收斂，直接擋掉幻覺標籤；順序沿用剪枝後的確定性順序
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
    """schema 形狀的穩定指紋。

    動態 schema 每換一組標籤就是一份新 grammar，指紋用於觀測 grammar 首編譯命中率，
    也讓 golden test 能鎖住組裝結果。
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
    """剔除不屬於該事件 concept 的屬性，防止事件間屬性互相滲透。

    與上游不同：非白名單欄位一律剔除，即使有值也不保留。上游為求保守會留下有值的
    無關欄位，但那正是屬性滲透的入口——血壓值被填進睡眠事件時，保留它只是把錯誤
    寫進 `structured_detail`。有值卻被剔除時記錄告警，作為調整 prompt 的訊號。
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
    """把組裝結果寫成 prompt 規則區塊。

    萃取階段不靠解碼層約束，因此 schema 規則必須在 prompt 裡講清楚：
    允許的分類節點、每個節點的屬性白名單、predicate 候選、null 政策，
    以及完整 JSON Schema。
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

    if predicate_candidates:
        lines.append("")
        lines.append("## predicate 候選詞彙")
        lines.append(
            f"predicate 必須從對應節點的候選清單中選一個；都不適用時填 `{other_predicate_token}`。"
        )
        for concept_id in composed.concept_ids:
            candidates = predicate_candidates.get(concept_id) or ()
            rendered = "、".join(f"`{c}`" for c in candidates) if candidates else "（無候選，請填上述其他值）"
            lines.append(f"- `{concept_id}`：{rendered}")

    lines.append("")
    lines.append("## 輸出規則")
    lines.append("1. 只輸出符合下方 JSON Schema 的 JSON，不要加說明文字或程式碼註解。")
    lines.append("2. 未在對話中提及的欄位一律填 `null`，不要推測、不要沿用其他事件的值。")
    lines.append("3. 同一件事只輸出一筆事件；不同主體或不同謂語則拆成多筆。")
    lines.append("4. `event_summary` 用精簡描述，不要複製整段逐字稿。")
    lines.append("5. `subject` 與 `predicate` 必填，兩者決定事件身分。")

    lines.append("")
    lines.append("## JSON Schema")
    lines.append("```json")
    lines.append(json.dumps(composed.schema_json, ensure_ascii=False, indent=2, sort_keys=True))
    lines.append("```")

    return "\n".join(lines)


def check_schema_constraints(schema: Mapping[str, Any]) -> list[str]:
    """檢查 JSON Schema 是否落在 Bedrock structured outputs 支援的子集內。

    回傳違規描述清單；空清單代表可安全啟用硬約束模式。
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
                violations.append(f"{path}: object 必須設定 additionalProperties=false")

        for key, value in node.items():
            walk(value, f"{path}.{key}")

    walk(schema, "$")
    return violations
