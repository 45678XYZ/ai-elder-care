"""可配置分類體系的載入與查詢。

事件分兩層分類（規範見 docs/framework.md 的「分類體系」）：
- 高階類別 `type`：對外契約，與 daily_summaries.sections 一一對應
- 細分類節點 `concept_id`：對內使用，來自可抽換的本體論資產

三份資產各自獨立，程式不硬編碼任何類別字串：
- `unified_care_ontology.json`：節點體系（階層、定義、同義詞、屬性）
- `high_level_types.json`：高階類別定義與預設類別
- `concept_type_map.json`：節點 → 高階類別映射，以及寫入 event 的 `taxonomy_version`

映射採「先精確、再沿祖先鏈」解析，因此只需登記能決定分類的層級，葉節點自動繼承；
個別葉節點需要覆寫時直接登記完整 `concept_id` 即可。都找不到時退回預設類別並告警，
不靜默丟棄事件。
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import json
import logging

from .config import TAXONOMY_ASSETS_DIR

logger = logging.getLogger(__name__)

ONTOLOGY_FILE = "unified_care_ontology.json"
HIGH_LEVEL_TYPES_FILE = "high_level_types.json"
CONCEPT_TYPE_MAP_FILE = "concept_type_map.json"
PROPERTY_REGISTRY_FILE = "property_registry.json"
SYNONYM_DICTIONARY_FILE = "synonym_dictionary.json"


class TaxonomyError(ValueError):
    """分類體系資產不一致；屬部署期錯誤，必須讓工作失敗而非降級執行。"""


@dataclass(frozen=True)
class ConceptNode:
    """本體論的單一節點。"""

    concept_id: str
    display_name: str
    display_name_en: str | None
    level: int
    is_leaf: bool
    definition: str
    retrieval_description: str
    parent: str | None
    children: tuple[str, ...]
    synonyms: tuple[str, ...]
    examples: tuple[str, ...]
    own_properties: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class HighLevelType:
    """對外的高階事件類別。"""

    id: str
    display_name: str
    description: str


@dataclass(frozen=True)
class Taxonomy:
    """已驗證的分類體系；不可變，可安全在 Lambda warm start 間重用。"""

    taxonomy_version: str
    ontology_version: str
    high_level_types_version: str
    default_type: str
    types: tuple[HighLevelType, ...]
    nodes: dict[str, ConceptNode]
    mappings: dict[str, str]
    property_registry: dict[str, Any]
    synonym_dictionary: dict[str, Any]

    # -- 高階類別 ------------------------------------------------------------

    @property
    def type_ids(self) -> tuple[str, ...]:
        """高階類別 ID，順序即為摘要呈現順序。"""
        return tuple(t.id for t in self.types)

    # -- 節點查詢 ------------------------------------------------------------

    def get(self, concept_id: str) -> ConceptNode | None:
        return self.nodes.get(concept_id)

    def ancestors(self, concept_id: str) -> tuple[str, ...]:
        """由該節點的父節點往上到根節點；節點不存在時回空 tuple。"""
        node = self.nodes.get(concept_id)
        if node is None:
            return ()
        chain: list[str] = []
        seen: set[str] = {concept_id}
        current = node.parent
        while current and current not in seen:
            chain.append(current)
            seen.add(current)
            parent_node = self.nodes.get(current)
            current = parent_node.parent if parent_node else None
        return tuple(chain)

    def leaf_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid, node in self.nodes.items() if node.is_leaf)

    # -- 分類解析 ------------------------------------------------------------

    def resolve_type(self, concept_id: str) -> tuple[str, str | None]:
        """回傳 `(高階類別, 命中映射的 concept_id)`。

        沿「先精確、再祖先鏈」解析；完全找不到時回傳預設類別且第二個值為 None，
        呼叫端可據此決定是否告警或計數。
        """
        mapped = self.mappings.get(concept_id)
        if mapped is not None:
            return mapped, concept_id
        for ancestor in self.ancestors(concept_id):
            mapped = self.mappings.get(ancestor)
            if mapped is not None:
                return mapped, ancestor
        return self.default_type, None

    def high_level_type(self, concept_id: str) -> str:
        """取得 `events.type`；無法映射時退回預設類別並告警。"""
        event_type, matched = self.resolve_type(concept_id)
        if matched is None:
            # 只告警不丟棄：分類體系擴充時新節點可能還沒登記映射，
            # 事件本身仍有價值，但必須留下痕跡以便補映射
            logger.warning(
                "concept_id 無法映射到高階類別，退回預設值：concept_id=%s default_type=%s taxonomy_version=%s",
                concept_id,
                self.default_type,
                self.taxonomy_version,
            )
        return event_type

    def unmapped_leaf_ids(self) -> tuple[str, ...]:
        """所有無法經精確或祖先鏈映射的葉節點；驗證用。"""
        return tuple(cid for cid in self.leaf_ids() if self.resolve_type(cid)[1] is None)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise TaxonomyError(f"分類體系資產缺失：{path}")
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def _build_nodes(raw: Any) -> dict[str, ConceptNode]:
    nodes_raw = raw.get("nodes") if isinstance(raw, dict) else None
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise TaxonomyError("本體論資產不含 nodes 陣列")

    nodes: dict[str, ConceptNode] = {}
    for entry in nodes_raw:
        concept_id = entry.get("concept_id")
        if not concept_id:
            raise TaxonomyError("本體論節點缺少 concept_id")
        if concept_id in nodes:
            raise TaxonomyError(f"本體論節點 concept_id 重複：{concept_id}")
        nodes[concept_id] = ConceptNode(
            concept_id=concept_id,
            display_name=entry.get("display_name") or concept_id,
            display_name_en=entry.get("display_name_en"),
            level=int(entry.get("level", concept_id.count("."))),
            is_leaf=bool(entry.get("is_leaf", not entry.get("children"))),
            definition=entry.get("definition") or "",
            retrieval_description=entry.get("label_description_for_retrieval") or "",
            parent=entry.get("parent"),
            children=tuple(entry.get("children") or ()),
            synonyms=tuple(entry.get("synonyms") or ()),
            examples=tuple(entry.get("examples") or ()),
            own_properties=tuple(entry.get("own_properties") or ()),
        )

    for node in nodes.values():
        if node.parent is not None and node.parent not in nodes:
            raise TaxonomyError(f"節點 {node.concept_id} 的 parent 不存在：{node.parent}")
        for child in node.children:
            if child not in nodes:
                raise TaxonomyError(f"節點 {node.concept_id} 的 child 不存在：{child}")
    return nodes


def _build_types(raw: Any) -> tuple[tuple[HighLevelType, ...], str, str]:
    types_raw = raw.get("types") if isinstance(raw, dict) else None
    if not isinstance(types_raw, list) or not types_raw:
        raise TaxonomyError("高階類別資產不含 types 陣列")

    types: list[HighLevelType] = []
    seen: set[str] = set()
    for entry in types_raw:
        type_id = entry.get("id")
        if not type_id:
            raise TaxonomyError("高階類別缺少 id")
        if type_id in seen:
            raise TaxonomyError(f"高階類別 id 重複：{type_id}")
        seen.add(type_id)
        types.append(
            HighLevelType(
                id=type_id,
                display_name=entry.get("display_name") or type_id,
                description=entry.get("description") or "",
            )
        )

    default_type = raw.get("default_type")
    if default_type not in seen:
        raise TaxonomyError(f"default_type 不在高階類別清單內：{default_type}")
    return tuple(types), default_type, raw.get("version") or ""


def load_taxonomy(assets_dir: Path | str | None = None) -> Taxonomy:
    """從資產目錄載入並驗證分類體系。

    資產內容在部署包內固定，因此依目錄快取；測試要抽換體系時傳入不同目錄即可，
    快取以路徑為 key 不會互相污染。
    """
    resolved = Path(assets_dir) if assets_dir is not None else TAXONOMY_ASSETS_DIR
    return _load_taxonomy_cached(str(resolved.resolve()))


@lru_cache(maxsize=8)
def _load_taxonomy_cached(assets_dir: str) -> Taxonomy:
    base = Path(assets_dir)
    ontology_raw = _read_json(base / ONTOLOGY_FILE)
    types_raw = _read_json(base / HIGH_LEVEL_TYPES_FILE)
    map_raw = _read_json(base / CONCEPT_TYPE_MAP_FILE)

    nodes = _build_nodes(ontology_raw)
    types, default_type, types_version = _build_types(types_raw)
    type_ids = {t.id for t in types}

    mappings = map_raw.get("mappings")
    if not isinstance(mappings, dict) or not mappings:
        raise TaxonomyError("映射資產不含 mappings 物件")
    for concept_id, type_id in mappings.items():
        if concept_id not in nodes:
            raise TaxonomyError(f"映射指向不存在的節點：{concept_id}")
        if type_id not in type_ids:
            raise TaxonomyError(f"映射指向未定義的高階類別：{concept_id} -> {type_id}")

    taxonomy_version = map_raw.get("taxonomy_version")
    if not taxonomy_version:
        raise TaxonomyError("映射資產缺少 taxonomy_version")

    taxonomy = Taxonomy(
        taxonomy_version=taxonomy_version,
        ontology_version=str((ontology_raw.get("metadata") or {}).get("version") or ""),
        high_level_types_version=types_version,
        default_type=default_type,
        types=types,
        nodes=nodes,
        mappings=dict(mappings),
        property_registry=_read_json(base / PROPERTY_REGISTRY_FILE),
        synonym_dictionary=_read_json(base / SYNONYM_DICTIONARY_FILE),
    )

    # 葉節點是實際會寫進 event 的分類；有任何一個落到預設類別就是資產漏登記，
    # 屬部署期錯誤，不能等到跑 batch 才用告警發現
    unmapped = taxonomy.unmapped_leaf_ids()
    if unmapped:
        raise TaxonomyError(f"以下葉節點無法映射到高階類別：{', '.join(unmapped)}")
    return taxonomy
