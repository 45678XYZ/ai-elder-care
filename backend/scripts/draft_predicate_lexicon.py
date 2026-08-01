"""謂語詞彙草稿產生器（已廢棄）。

自從採用開放世界謂語策略 (Open-World Predicate) 後，不再維護 concept → predicate 的映射表。
謂語由 LLM 自由生成，同義收斂在去重階段由 embedding similarity 處理。

predicate_lexicon.json 現在只維護主體別名 (subject_aliases)。
"""

import sys


def main(argv: list[str]) -> int:
    print("此腳本已廢棄。")
    print()
    print("原因：採用開放世界謂語策略後，不再維護 concept_id → predicate 的映射表。")
    print("謂語由 LLM 自由撰寫，同義收斂由 dedup.py 的 embedding similarity 處理。")
    print()
    print("predicate_lexicon.json 現在只維護：")
    print("  - version: 版本號")
    print("  - other_token: 保留字")
    print("  - subject_aliases: 主體別名映射（如「阿嬤」→「長者」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
