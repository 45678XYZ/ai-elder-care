"""可配置分類體系的載入與查詢模組。

提供兩層事件分類體系（對內細分類 `concept_id` 與對外高階類別 `type`）的動態載入、
階層解析與完整性校驗。架構規範與規格詳見 `docs/framework.md` 的「分類體系」章節。

三份資產各自獨立，程式不硬編碼任何類別字串：
- `unified_care_ontology.json`：節點體系（階層、定義、同義詞、屬性）
- `high_level_types.json`：高階類別定義與預設類別
- `concept_type_map.json`：節點 → 高階類別映射，以及寫入 event 的 `taxonomy_version`

本模組設計目的：
- 隔離程式邏輯與本體論資產（JSON），擴充或修正分類時無需修改 Python 程式碼。
- 提供「精確匹配優先、父祖鏈繼承次之」的解析機制，簡化資產映射檔的維護成本。
- 於載入階段進行資產結構完整性校驗，確保部署期即抓出未映射節點，避免運行時錯誤。
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import json
import logging

TAXONOMY_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "taxonomy"

logger = logging.getLogger(__name__)

# 資產檔案名稱常數：獨立管理本體論樹狀結構、高階類別、映射關係與同義詞典
ONTOLOGY_FILE = "unified_care_ontology.json"
HIGH_LEVEL_TYPES_FILE = "high_level_types.json"
CONCEPT_TYPE_MAP_FILE = "concept_type_map.json"
PROPERTY_REGISTRY_FILE = "property_registry.json"
SYNONYM_DICTIONARY_FILE = "synonym_dictionary.json"


PSEUDO_CONCEPT_PREFIX = "UCO.HighLevel."


def pseudo_concept_id(type_id: str) -> str:
    """組出某個 High_Level_Type id 對應的虛擬分類節點 concept_id。"""
    return f"{PSEUDO_CONCEPT_PREFIX}{type_id}"


class TaxonomyError(ValueError):
    """分類體系資產不一致錯誤。

    此類錯誤源於資產檔配置缺失或關聯破壞，屬於部署階段即可發覺的配置問題，
    必須直接使模組載入失敗阻斷部署，而非於運行階段進行降級處理。
    """


@dataclass(frozen=True)
class ConceptNode:
    """本體論階層樹中的單一節點定義。

    採用 `frozen=True` 確保節點定義在記憶體中具不可變性（Immutable），
    避免多執行緒或跨請求呼叫時誤修改本體論結構。
    """

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
    """對外公開的高階事件類別定義。

    對應前端畫面呈現與每日摘要區塊 (`daily_summaries.sections`) 的分類標籤。
    詳見 `docs/framework.md`。
    """

    id: str
    display_name: str
    description: str


@dataclass(frozen=True)
class Taxonomy:
    """已校驗且封裝完畢的不可變分類體系實例。

    設計為不可變物件，可安全地在 AWS Lambda Warm Start 容器生命週期中被多個請求重用。
    """

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
        """取得高階類別 ID 列表。

        傳回順序維持 `high_level_types.json` 定義之順序，此順序決定 UI 與摘要呈現時的區域順序。
        """
        return tuple(t.id for t in self.types)

    # -- 節點查詢 ------------------------------------------------------------

    def get(self, concept_id: str) -> ConceptNode | None:
        """依據 concept_id 取得節點實例，若不存在則傳回 None。

        Pseudo concept（UCO.HighLevel.*）視為合法存在，回傳虛擬節點。
        """
        node = self.nodes.get(concept_id)
        if node is not None:
            return node
        if self.is_pseudo_concept(concept_id):
            type_id = concept_id[len(PSEUDO_CONCEPT_PREFIX):]
            return ConceptNode(
                concept_id=concept_id, display_name=type_id,
                display_name_en=None, level=1, is_leaf=True,
                definition="", retrieval_description="",
                parent=None, children=(), synonyms=(),
                examples=(), own_properties=(),
            )
        return None

    def ancestors(self, concept_id: str) -> tuple[str, ...]:
        """取得由指定節點之父節點一路向上至根節點的祖先鏈。

        提供自底向上的節點回溯，用於類別映射繼承解析。內建 `seen` 集合以防止資產壞軌時出現無限循環。
        """
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
        """取得所有葉節點（is_leaf=True）的 ID 列表。

        葉節點為實際被事件萃取 Pipeline 輸出的最小分類單元，用於部署期完整性校驗。
        """
        return tuple(cid for cid, node in self.nodes.items() if node.is_leaf)

    # -- 分類解析 ------------------------------------------------------------

    def resolve_type(self, concept_id: str) -> tuple[str, str | None]:
        """將細分類 `concept_id` 解析為對外高階類別 `type`。

        傳回 `(高階類別, 命中映射的 concept_id)`。
        解析採用「先精確匹配、再沿祖先鏈回溯」策略，減少重複在映射檔中登記所有子節點的維護成本。
        若沿祖先鏈皆無匹配，則傳回預設類別且第二個元素為 None。
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
        """取得寫入 `events.type` 的高階類別字串。

        Pseudo concept 直接回傳其對應的 type_id；一般節點沿映射表解析。
        """
        if concept_id.startswith(PSEUDO_CONCEPT_PREFIX):
            type_id = concept_id[len(PSEUDO_CONCEPT_PREFIX):]
            return type_id if type_id in self.type_ids else self.default_type
        event_type, matched = self.resolve_type(concept_id)
        if matched is None:
            logger.warning(
                "concept_id 無法映射到高階類別，退回預設值：concept_id=%s default_type=%s taxonomy_version=%s",
                concept_id,
                self.default_type,
                self.taxonomy_version,
            )
        return event_type

    def unmapped_leaf_ids(self) -> tuple[str, ...]:
        """找出無法透過精確匹配或祖先鏈映射至高階類別的所有葉節點。

        主要用於 `load_taxonomy` 靜態校驗，避免遺漏映射的資產部署至線上環境。
        """
        return tuple(cid for cid in self.leaf_ids() if self.resolve_type(cid)[1] is None)

    # -- Pseudo concept 查詢 --------------------------------------------------

    def is_pseudo_concept(self, concept_id: str) -> bool:
        """判斷 concept_id 是否為某個 High_Level_Type 的虛擬分類節點。"""
        if not concept_id.startswith(PSEUDO_CONCEPT_PREFIX):
            return False
        type_id = concept_id[len(PSEUDO_CONCEPT_PREFIX):]
        return type_id in self.type_ids

    def pseudo_concept_for_label(self, label: str) -> tuple[str, bool]:
        """把模型回傳的標籤字串映射為 (pseudo concept_id, 是否為合法標籤)。"""
        if label in self.type_ids:
            return pseudo_concept_id(label), True
        return pseudo_concept_id(self.default_type), False


def _read_json(path: Path) -> Any:
    """讀取並解析 JSON 資產檔。若檔案不存在則判定為部署檔缺失，拋出 TaxonomyError。"""
    if not path.is_file():
        raise TaxonomyError(f"分類體系資產缺失：{path}")
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def _build_nodes(raw: Any) -> dict[str, ConceptNode]:
    """解析並構建本體論節點字典，同步校驗雙向樹狀關聯。

    於構建時強制檢查 parent 與 children 節點存在性，避免無效指標破壞後續祖先鏈走訪。
    """
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

    # 校驗樹狀關聯雙向指標完整性，預防懸空節點破壞祖先樹遍歷
    for node in nodes.values():
        if node.parent is not None and node.parent not in nodes:
            raise TaxonomyError(f"節點 {node.concept_id} 的 parent 不存在：{node.parent}")
        for child in node.children:
            if child not in nodes:
                raise TaxonomyError(f"節點 {node.concept_id} 的 child 不存在：{child}")
    return nodes


def _build_types(raw: Any) -> tuple[tuple[HighLevelType, ...], str, str]:
    """解析並構建高階類別定義，確保 default_type 落在合法清單中。"""
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
    """載入並驗證分類體系實例。

    透過解析後的絕對路徑字串作為 LRU 快取 Key，能在 AWS Lambda Warm Start 期間
    避免重複讀取解析 JSON 檔，同時允許測試案例傳入自訂資產目錄而不致發生快取污染。
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

    # 葉節點是實際會被萃取出來寫入事件的分類單元；若於載入階段發現無對應高階類別，
    # 屬於資產漏登記的部署期錯誤，必須立即中斷載入，絕不能延遲至批次處理階段才用警告發現。
    unmapped = taxonomy.unmapped_leaf_ids()
    if unmapped:
        raise TaxonomyError(f"以下葉節點無法映射到高階類別：{', '.join(unmapped)}")
    return taxonomy

