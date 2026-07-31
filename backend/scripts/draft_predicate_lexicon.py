"""從本體論產生謂語詞彙草稿，供人工整理後併入 predicate_lexicon.json。

刻意只產草稿而不直接當成執行期資產：本體論的 `synonyms` 混雜名詞與 ICF 機能名稱
（例如 `VitalSignRecord` 的同義詞含「循環與呼吸機能」），直接拿來當謂語會讓
canonical key 出現不成句的值。這支腳本負責把候選攤出來、標示現有詞彙的缺口，
判斷仍由人做。

    python -m scripts.draft_predicate_lexicon            # 只列出缺口
    python -m scripts.draft_predicate_lexicon --all      # 列出所有節點的候選
"""

import argparse
import json
import sys

from src.extraction.canonical import load_predicate_lexicon, validate_lexicon
from src.extraction.taxonomy import load_taxonomy


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="產生謂語詞彙草稿")
    parser.add_argument("--all", action="store_true", help="列出所有節點，而非只列缺口")
    args = parser.parse_args(argv[1:])

    taxonomy = load_taxonomy()
    lexicon = load_predicate_lexicon()

    problems = validate_lexicon(lexicon, taxonomy)
    print(f"lexicon_version : {lexicon.version}")
    print(f"已登記節點      : {len(lexicon.concepts)}")
    print(f"一致性問題      : {len(problems)}")
    for problem in problems:
        print(f"  ! {problem}")
    print()

    draft: dict[str, dict[str, object]] = {}
    for concept_id, node in sorted(taxonomy.nodes.items()):
        if node.level < 2:
            continue
        registered = lexicon.candidates(concept_id)
        if registered and not args.all:
            continue
        draft[concept_id] = {
            "display_name": node.display_name,
            "definition": node.definition,
            "registered_canonical": list(registered),
            "synonym_candidates": list(node.synonyms),
        }

    if not draft:
        print("沒有缺口，所有可分類節點都已登記謂語候選。")
        return 0

    print("以下節點需要人工補上 canonical 謂語（synonym_candidates 僅供參考，多為名詞）：")
    print(json.dumps(draft, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
