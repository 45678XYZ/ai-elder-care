"""印出分類體系的高階類別定義。

用途是快速確認資產抽換或擴充後的映射結果，也是 Task 2 的驗收 demo。

    python -m scripts.dump_taxonomy                 # 用部署包內的正本資產
    python -m scripts.dump_taxonomy path/to/assets  # 用替換後的資產目錄
"""

import sys

from src.extraction.taxonomy import load_taxonomy


def main(argv: list[str]) -> int:
    taxonomy = load_taxonomy(argv[1] if len(argv) > 1 else None)

    print(f"taxonomy_version      : {taxonomy.taxonomy_version}")
    print(f"high_level_types      : {', '.join(taxonomy.type_ids)}")
    print(f"default_type          : {taxonomy.default_type}")
    print()

    print("高階類別：")
    for high_level_type in taxonomy.types:
        print(f"  {high_level_type.id:<12} {high_level_type.display_name:<8} | {high_level_type.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
