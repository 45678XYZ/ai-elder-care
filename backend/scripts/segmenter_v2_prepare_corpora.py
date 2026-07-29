"""步驟 1：把 Def-DTS 語料正規化成本工作流格式。

    python scripts/segmenter_v2_prepare_corpora.py

輸出（`data/segmenter_v2/`）：
    train_en.jsonl   tiage_train + tiage_validation + dialseg711_test（真人標註、英文）
    dev_en.jsonl     tiage_test（調門檻與選特徵用）

為什麼訓練集用英文：真人標註的繁中切分語料在這個 repo 裡只有 3 段，量級不足以訓練。
英文真標有 1100+ 段，配上多語言 embedding 走跨語言零樣本遷移（CobSeg 已驗證這條路可行）。
下一步 `segmenter_v2_translate.py` 會把這些對話逐 turn 翻成繁中，標籤位置原封不動沿用。

刻意不納入 `data/clean_pairwise_dataset.jsonl`：其 SeniorTalk 部分是
`(i + 1) % 4 == 0` 的機械假標，拿來訓練等於教模型「每 4 輪切一次」。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import corpora  # noqa: E402
from training.segmenter_v2 import paths  # noqa: E402

DTS_DIR = paths.DTS_SESSION_DIR
OUTPUT_DIR = paths.CORPORA_DIR

TRAIN_SOURCES = ("tiage_train.jsonl", "tiage_validation.jsonl", "dialseg711_test.jsonl")
DEV_SOURCES = ("tiage_test.jsonl",)
# superseg 分布與其他兩者不同，留作後續擴充，預設不納入
OPTIONAL_SOURCES = ("superseg_train.jsonl", "superseg_validation.jsonl", "superseg_test.jsonl")


def collect(names, dts_dir: Path, *, limit_per_file=None):
    dialogues = []
    for name in names:
        path = dts_dir / name
        if not path.is_file():
            print(f"  ! 找不到 {path}，略過")
            continue
        loaded = corpora.load_dts_jsonl(path, corpus=path.stem, limit=limit_per_file)
        print(f"  {name:<28} {len(loaded):>5} 段")
        dialogues.extend(loaded)
    return dialogues


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="正規化 Def-DTS 語料")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument(
        "--dts-dir",
        default=str(DTS_DIR),
        help="Def-DTS session datasets 目錄；第三方語料不複製進本 repo，預設指向 aws-hackathon 的既有副本",
    )
    parser.add_argument("--limit-per-file", type=int, default=None, help="每個檔案只取前 N 段（除錯用）")
    parser.add_argument("--include-superseg", action="store_true")
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir)
    dts_dir = Path(args.dts_dir)
    print(paths.describe())
    print()
    if not dts_dir.is_dir():
        raise SystemExit(
            f"找不到 Def-DTS session datasets：{dts_dir}\n"
            "用 --dts-dir 或環境變數 DTS_SESSION_DATASETS 指定位置。"
        )

    print("訓練集：")
    train_names = TRAIN_SOURCES + (OPTIONAL_SOURCES if args.include_superseg else ())
    train = collect(train_names, dts_dir, limit_per_file=args.limit_per_file)

    print("開發集：")
    dev = collect(DEV_SOURCES, dts_dir, limit_per_file=args.limit_per_file)

    # 同一段對話不得同時出現在訓練與開發集；切分一律 by dialogue_id
    dev_ids = {dialogue.dialogue_id for dialogue in dev}
    overlap = [dialogue.dialogue_id for dialogue in train if dialogue.dialogue_id in dev_ids]
    if overlap:
        raise SystemExit(f"訓練集與開發集重疊 {len(overlap)} 段，請檢查來源檔案")

    corpora.save_dialogues(output_dir / "train_en.jsonl", train)
    corpora.save_dialogues(output_dir / "dev_en.jsonl", dev)

    summary = {"train": corpora.describe(train), "dev": corpora.describe(dev)}
    (output_dir / "corpora_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
