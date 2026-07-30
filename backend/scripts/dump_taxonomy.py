"""印出分類體系的節點 → 高階類別對照表。

用途是快速確認資產抽換或擴充後的映射結果，也是 Task 2 的驗收 demo。

    python -m scripts.dump_taxonomy                 # 用部署包內的正本資產
    python -m scripts.dump_taxonomy path/to/assets  # 用替換後的資產目錄
"""

import sys
from collections import Counter

from src.extraction.taxonomy import load_taxonomy


def main(argv: list[str]) -> int:
    taxonomy = load_taxonomy(argv[1] if len(argv) > 1 else None)

    print(f"taxonomy_version      : {taxonomy.taxonomy_version}")
    print(f"ontology_version      : {taxonomy.ontology_version}")
    print(f"high_level_types      : {', '.join(taxonomy.type_ids)}")
    print(f"default_type          : {taxonomy.default_type}")
    print(f"nodes / leaves        : {len(taxonomy.nodes)} / {len(taxonomy.leaf_ids())}")
    print()

    counts: Counter[str] = Counter()
    for concept_id in sorted(taxonomy.nodes):
        event_type, matched = taxonomy.resolve_type(concept_id)
        node = taxonomy.nodes[concept_id]
        indent = "  " * node.level
        via = "預設回退" if matched is None else ("直接登記" if matched == concept_id else f"繼承自 {matched}")
        print(f"{event_type:<10} | {indent}{concept_id}（{node.display_name}）| {via}")
        if node.is_leaf:
            counts[event_type] += 1

    print("\n葉節點分佈：")
    for type_id in taxonomy.type_ids:
        print(f"  {type_id:<10} {counts.get(type_id, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
