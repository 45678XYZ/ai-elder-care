"""Top-Down 階層多標籤剪枝（HMLC）。

兩條規則來自 aws-hackathon 的 `hmlc_pruner`，語意不變：

1. **葉節點特異性優先**：葉節點命中時，壓制其所有祖先節點，避免同一件事同時被標成
   「用藥行為」與「藥物不良反應」而在下游產生兩筆事件。
2. **父節點籠統退守**：類別節點命中但其下沒有任何葉節點命中時，保留該類別節點作為
   籠統分類，不因為「不夠具體」就丟掉事件。

移植時新增兩項約束：

- **輸出必須確定性**：batch 的冪等性建立在同一 snapshot 產生同一組 canonical key 上，
  因此剪枝結果一律以 `(信心值遞減, concept_id)` 排序，不沿用輸入順序。
- **層級下限**：根節點與第一層領域節點（如 `UCO.BehavioralRecord`）粗到無法對應高階
  類別，也無法組出有意義的屬性 schema，一律丟棄並告警。
"""

from collections.abc import Iterable, Sequence
import logging

from .models import LabelHit
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 可作為事件分類的最淺層級；UCO=0、UCO.BehavioralRecord=1、類別節點=2
MIN_CLASSIFIABLE_LEVEL = 2


def prune_label_hits(
    hits: Iterable[LabelHit],
    taxonomy: Taxonomy,
    *,
    min_confidence: float = 0.0,
    min_level: int = MIN_CLASSIFIABLE_LEVEL,
) -> tuple[LabelHit, ...]:
    """對命中標籤做階層剪枝，回傳確定性排序後的結果。"""
    accepted: dict[str, LabelHit] = {}

    for hit in hits:
        node = taxonomy.get(hit.concept_id)
        if node is None:
            # 分類器可能幻覺出不存在的節點；丟棄但留痕，供調整候選集與 prompt
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

        # 同一節點重複命中時保留信心值較高者，避免下游算出不同 confidence
        existing = accepted.get(hit.concept_id)
        if existing is None or hit.confidence > existing.confidence:
            accepted[hit.concept_id] = hit

    if not accepted:
        return ()

    # 葉節點命中即壓制其祖先鏈；未被壓制的類別節點屬於「父節點退守」
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

    return tuple(sorted(pruned, key=lambda h: (-h.confidence, h.concept_id)))


def concept_ids(hits: Sequence[LabelHit]) -> tuple[str, ...]:
    """取出 concept_id 序列，保持與 hits 相同順序。"""
    return tuple(hit.concept_id for hit in hits)
