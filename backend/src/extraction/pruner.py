"""Top-Down 階層多標籤剪枝 (HMLC - Hierarchical Multi-Label Classification) 模組。

提供對 LLM 識別出的多標籤進行本體論階層樹的剪枝與去重。
架構規範與設計決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

本模組設計目的與兩大 HMLC 剪枝原則：
1. **葉節點特異性優先（壓制祖先）**：當特異性高的葉節點命中時，自動壓制其祖先鏈節點，防止同一生活行為（如「按時吃血壓藥」）同時寫入一般類別與具體葉節點，導致下游重複建立兩筆事件。
2. **類別節點籠統退守（父節點保留）**：當長者提及主題但細節不足以歸類至特定葉節點時，保留該類別節點作為退守分類，防止僅因「不夠具體」就遺失生活紀錄。

品質與複製性約束：
- **極小化層級限制 (MIN_CLASSIFIABLE_LEVEL = 2)**：剔除過於粗粒度、無法映射至 7 大高階事件類別的根節點與一級領域節點。
- **確定性排序**：剪枝結果統一按 `(信心值降序, concept_id 字典序)` 排序，保障批次萃取與 Canonical Key 具備冪等重試再現性。
"""

from collections.abc import Iterable, Sequence
import logging

from .models import LabelHit
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 可作為事件分類的最淺階層門檻（UCO 根節點為 Level 0，領域大類如 UCO.BehavioralRecord 為 Level 1）；
# 低於此門檻之節點過於粗粒度，無法映射至 7 大高階類別，亦無法構建具體的屬性 Schema。
MIN_CLASSIFIABLE_LEVEL = 2


def prune_label_hits(
    hits: Iterable[LabelHit],
    taxonomy: Taxonomy,
    *,
    min_confidence: float = 0.0,
    min_level: int = MIN_CLASSIFIABLE_LEVEL,
) -> tuple[LabelHit, ...]:
    """對識別出的命中標籤執行階層剪枝與確定性排序。

    先過濾未知節點、過淺節點與低信心度標籤；接著尋找所有命中的葉節點，
    將其父祖鏈節點加入壓制清單；最後將未被壓制之標籤進行確定性排序後傳回。
    """
    accepted: dict[str, LabelHit] = {}

    for hit in hits:
        node = taxonomy.get(hit.concept_id)
        if node is None:
            # 防範無約束降級路徑傳回未在分類體系中定義之節點，留下記錄以供檢視與維護
            logger.warning("剪枝丟棄未知節點：concept_id=%s", hit.concept_id)
            continue
        if node.level < min_level:
            logger.warning(
                "剪枝丟棄層級過淺的節點：concept_id=%s level=%s min_level=%s",
                hit.concept_id,
                node.level,
                min_level,
            )
            continue
        if hit.confidence < min_confidence:
            continue

        # 相同節點被多次傳入時保留最高信心值者，避免信心值不一致影響排序確定性
        existing = accepted.get(hit.concept_id)
        if existing is None or hit.confidence > existing.confidence:
            accepted[hit.concept_id] = hit

    if not accepted:
        return ()

    # 當細粒度葉節點命中時，其所有父祖節點均劃入壓制集合，防止下游重複產生高階與細階兩筆事件
    suppressed: set[str] = set()
    for concept_id in accepted:
        node = taxonomy.nodes[concept_id]
        if node.is_leaf:
            suppressed.update(taxonomy.ancestors(concept_id))

    pruned = []
    for concept_id, hit in accepted.items():
        if concept_id in suppressed:
            logger.info("剪枝壓制祖先節點：concept_id=%s（其下已有葉節點命中）", concept_id)
            continue
        pruned.append(hit)

    # 按信心值由高至低、同分按字典序排序，確保動態 Schema 與 Canonical Key 計算具重複再現性
    return tuple(sorted(pruned, key=lambda h: (-h.confidence, h.concept_id)))


def concept_ids(hits: Sequence[LabelHit]) -> tuple[str, ...]:
    """從 LabelHit 序列中提取 concept_id 列表，保持傳入順序。

    供 Schema Composer 與 Pipeline 便捷提取節點 ID 序列使用。
    """
    return tuple(hit.concept_id for hit in hits)

