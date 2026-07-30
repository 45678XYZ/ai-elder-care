"""步驟 5：產生人工標註檔（測試集三層）。

    python scripts/segmenter_v2_prepare_annotation.py --tier real --source <seniortalk_raw.jsonl> --count 20
    python scripts/segmenter_v2_prepare_annotation.py --tier localized --count 10
    python scripts/segmenter_v2_prepare_annotation.py --tier scenario --count 12

三層的意義（gate 只看 Test-Real）：

| 層 | 對話來源 | 用途 |
|---|---|---|
| `real` | `BAAI/SeniorTalk` 原始轉錄（只抽樣、清洗、編號，不改字） | gate 主判定 |
| `localized` | `seniortalk_tw_balanced_corpus.jsonl`（真實結構 + LLM 在地化用詞） | 輔助，指標分開報 |
| `scenario` | `balanced_corpus.json`（LLM 生成的長照場景） | 輔助，**不列入 gate** |

第三方語料不複製進本 repo（授權與體積考量），預設從 `paths.UPSTREAM_DATA_DIR` 讀取，
可用環境變數 `SEGMENTER_V2_UPSTREAM_DATA` 或 `--source` 覆寫。

分界線是「訓練資料可以機器產，評測資料不行」。合成對話的問題不是品質差，是反過來——
邊界會被寫得過於乾淨，指標虛高；而且產生者與稽核者同源，偏誤同源等於沒審。

輸出（`data/segmenter_v2/annotation/`）：
    <tier>_to_annotate.jsonl   `boundary_after` 留空，人工填 true/false
    <tier>_to_annotate.txt     純文字版，方便在編輯器裡掃過
    <tier>_guidelines.md       標註判準（單一標註者算不了 IAA，判準要盡量寫死）

標完後用 `--finalize` 轉成評測集格式，並自動跑污染檢查。
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import corpora, paths  # noqa: E402

OUTPUT_DIR = paths.ANNOTATION_DIR

TIER_CONFIG = {
    "real": {
        "text_source": corpora.TEXT_NATIVE,
        "default_source": paths.WORK_DIR / "seniortalk_raw.jsonl",
        "note": "BAAI/SeniorTalk 原始轉錄，未經 LLM 改寫；gate 主判定",
    },
    "localized": {
        "text_source": corpora.TEXT_LLM_LOCALIZED,
        "default_source": paths.UPSTREAM_DATA_DIR / "seniortalk_tw_balanced_corpus.jsonl",
        "note": "真實對話結構 + LLM 在地化用詞；輔助指標",
    },
    "scenario": {
        "text_source": corpora.TEXT_LLM_GENERATED,
        "default_source": paths.UPSTREAM_DATA_DIR / "balanced_corpus.json",
        "note": "LLM 生成的長照場景；不列入 gate",
    },
}

GUIDELINES = """# 對話主題邊界標註指引（{tier}）

對話來源：{note}

單一標註者無法計算標註者間一致性（IAA），因此判準要盡量寫死；標完後隔一段時間重標其中
5 段，算 self-agreement，作為結論可信度的下限。

## 算邊界

- **照護目標轉移**：從「睡眠與休息」轉到「用藥／生理量測」或「飲食水分」。
- **時間場景切換**：從「今天早上發生的事」轉到「上週回診」。

## 不算邊界

- 同一話題內的細節追問（「幾點吃的？」「吃了幾顆？」）。
- 寒暄、附和、確認（「嗯嗯」「好」「是喔」）。
- 同一話題內的重複詢問。

## QA Pair Closure 原則

話題切換時，**新話題的起點是提問那一輪，不是回答那一輪**。
把提問留在前一段、回答放到下一段，會讓「有啊」「吃過了」這種答句失去問題脈絡。

    Turn 4  AI：早上有量血壓嗎？      ← 邊界在這裡（boundary_after: Turn 3）
    Turn 5  長者：有啊，135 跟 85。

## 答非所問怎麼處理

長者答非所問時，以**提問所開啟的話題**為準：提問輪仍是新話題起點；若長者的回答又開了
第三個話題，則在回答那一輪再標一個邊界。

## 填法

`boundary_after` 為 true 表示「這一輪之後是新話題的起點」。最後一輪一律 false。
"""


def load_source_dialogues(path: Path) -> list[dict]:
    """盡量寬鬆地讀各種既有語料格式，只取出 turn 文字序列。"""
    raw_text = path.read_text(encoding="utf-8")
    items: list[dict] = []
    if path.suffix == ".json":
        payload = json.loads(raw_text)
        items = payload if isinstance(payload, list) else payload.get("scenarios") or payload.get("items") or []
    else:
        for line in raw_text.splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def extract_turns(item: dict) -> list[tuple[str, str]]:
    """從各種欄位命名中取出 `(speaker, text)`。"""
    for key in ("turns", "dialogue", "utterances", "conversation"):
        value = item.get(key)
        if isinstance(value, list):
            turns = []
            for entry in value:
                if isinstance(entry, dict):
                    speaker = entry.get("speaker") or entry.get("role") or "長者"
                    text = entry.get("text") or entry.get("content") or ""
                else:
                    speaker, _, text = str(entry).partition(":")
                    if not text:
                        speaker, text = "長者", str(entry)
                if str(text).strip():
                    turns.append((str(speaker).strip(), str(text).strip()))
            if turns:
                return turns
        if isinstance(value, str) and value.strip():
            turns = []
            for line in value.replace(corpora.NEWLINE_TOKEN, "\n").splitlines():
                line = line.strip()
                if not line or line == corpora.BOUNDARY_TOKEN:
                    continue
                speaker, _, text = line.partition(":")
                if not text:
                    speaker, text = "長者", line
                turns.append((speaker.strip(), text.strip()))
            if turns:
                return turns
    return []


def write_annotation_files(tier: str, dialogues: list[corpora.Dialogue], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = TIER_CONFIG[tier]

    jsonl_path = output_dir / f"{tier}_to_annotate.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fp:
        for dialogue in dialogues:
            fp.write(
                json.dumps(
                    {
                        "dialogue_id": dialogue.dialogue_id,
                        "text_source": dialogue.text_source,
                        "turns": [
                            {
                                "index": index,
                                "speaker": turn.speaker,
                                "text": turn.text,
                                # 人工填：這一輪之後是不是新話題的起點
                                "boundary_after": None,
                            }
                            for index, turn in enumerate(dialogue.turns)
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    text_path = output_dir / f"{tier}_to_annotate.txt"
    lines: list[str] = []
    for dialogue in dialogues:
        lines.append(f"===== {dialogue.dialogue_id}（{dialogue.turn_count} turns）=====")
        for index, turn in enumerate(dialogue.turns):
            lines.append(f"[Turn {index:02d}] {turn.speaker}：{turn.text}")
            lines.append("        boundary_after: [ ]")
        lines.append("")
    text_path.write_text("\n".join(lines), encoding="utf-8")

    (output_dir / f"{tier}_guidelines.md").write_text(
        GUIDELINES.format(tier=tier, note=config["note"]), encoding="utf-8"
    )
    print(f"已產生：\n  {jsonl_path}\n  {text_path}\n  {output_dir / f'{tier}_guidelines.md'}")


def finalize(tier: str, annotated_path: Path, output_path: Path, train_paths: list[str]) -> None:
    """把標註結果轉成評測集，並跑污染檢查。"""
    dialogues: list[corpora.Dialogue] = []
    unlabelled: list[str] = []
    with annotated_path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            turns_raw = item["turns"]
            if any(turn.get("boundary_after") is None for turn in turns_raw[:-1]):
                unlabelled.append(item["dialogue_id"])
                continue
            turns = [
                corpora.make_turn(item["dialogue_id"], index, turn["speaker"], turn["text"])
                for index, turn in enumerate(turns_raw)
            ]
            boundaries = [0] + [
                index + 1 for index, turn in enumerate(turns_raw[:-1]) if turn.get("boundary_after")
            ]
            dialogues.append(
                corpora.Dialogue(
                    dialogue_id=item["dialogue_id"],
                    turns=turns,
                    boundaries=boundaries,
                    language="zh-TW",
                    # 標籤是人工標的，這是 gate 判定成立的前提
                    label_source=corpora.LABEL_HUMAN,
                    text_source=item.get("text_source", TIER_CONFIG[tier]["text_source"]),
                    corpus=f"test_{tier}",
                )
            )

    if unlabelled:
        print(f"! 以下 {len(unlabelled)} 段尚未標完，已跳過：{unlabelled[:5]}")

    contaminated = check_contamination(dialogues, train_paths)
    if contaminated:
        print(f"! 污染警告：{len(contaminated)} 段與訓練／翻譯集重疊：{contaminated[:5]}")
        print("  評測集必須與訓練集完全分離，請換掉這些對話")

    count = corpora.save_dialogues(output_path, dialogues)
    print(f"已寫入評測集 {output_path}（{count} 段）")
    print(json.dumps(corpora.describe(dialogues), ensure_ascii=False, indent=2))


def check_contamination(dialogues, train_paths, *, ngram: int = 8) -> list[str]:
    """n-gram 重疊檢查：評測對話不得出現在訓練或翻譯集內。"""
    if not train_paths:
        return []
    train_ngrams: set[str] = set()
    for path in train_paths:
        resolved = Path(path)
        if not resolved.is_file():
            continue
        for dialogue in corpora.load_dialogues(resolved):
            for turn in dialogue.turns:
                text = turn.text
                for start in range(max(1, len(text) - ngram + 1)):
                    train_ngrams.add(text[start : start + ngram])

    contaminated = []
    for dialogue in dialogues:
        hits = 0
        total = 0
        for turn in dialogue.turns:
            text = turn.text
            for start in range(max(1, len(text) - ngram + 1)):
                total += 1
                if text[start : start + ngram] in train_ngrams:
                    hits += 1
        if total and hits / total > 0.5:
            contaminated.append(dialogue.dialogue_id)
    return contaminated


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="產生／收回人工標註檔")
    parser.add_argument("--tier", required=True, choices=sorted(TIER_CONFIG))
    parser.add_argument("--source", default="", help="對話來源；留空用該層預設")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--min-turns", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--finalize", default="", help="已標註的 JSONL；給了就轉成評測集")
    parser.add_argument(
        "--train-paths",
        nargs="*",
        default=[
            str(paths.CORPORA_DIR / "train_zh.jsonl"),
            str(paths.CORPORA_DIR / "dev_zh.jsonl"),
        ],
        help="污染檢查比對的訓練／翻譯集",
    )
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir)
    if args.finalize:
        finalize(
            args.tier,
            Path(args.finalize),
            output_dir.parent / f"test_{args.tier}_zh.jsonl",
            args.train_paths,
        )
        return 0

    source_path = Path(args.source or TIER_CONFIG[args.tier]["default_source"])
    if not source_path.is_file():
        print(f"找不到來源語料：{source_path}")
        if args.tier == "real":
            print(
                "Test-Real 需要 BAAI/SeniorTalk 的原始轉錄（未經 LLM 在地化改寫），\n"
                f"請自行下載後放到 {TIER_CONFIG['real']['default_source']}。\n"
                "授權已查證為 CC BY-NC-SA 4.0 + 學術非商業 gated 條款：\n"
                "  - 僅供研究評測，不得進訓練集，不得用於商業版本\n"
                "  - 衍生的標註集若要公開，須以相同授權釋出\n"
                "  - 禁止再識別受訪者\n"
                "詳見 docs/feature_segmenter-pairwise-v2.md 的授權章節。"
            )
        return 1

    items = load_source_dialogues(source_path)
    candidates = []
    for index, item in enumerate(items):
        turns = extract_turns(item)
        if len(turns) < args.min_turns:
            continue
        dialogue_id = str(item.get("id") or item.get("dialogue_id") or f"{args.tier}_{index:03d}")
        candidates.append(
            corpora.Dialogue(
                dialogue_id=dialogue_id,
                turns=[
                    corpora.make_turn(dialogue_id, position, speaker, text)
                    for position, (speaker, text) in enumerate(turns)
                ],
                boundaries=[0],
                language="zh-TW",
                label_source="pending-human-annotation",
                text_source=TIER_CONFIG[args.tier]["text_source"],
                corpus=f"test_{args.tier}",
            )
        )

    if not candidates:
        print(f"來源語料沒有長度 >= {args.min_turns} 的對話")
        return 1

    random.Random(args.seed).shuffle(candidates)
    selected = candidates[: args.count]
    print(f"來源 {source_path}：{len(items)} 筆 → 可用 {len(candidates)} 段 → 抽出 {len(selected)} 段")
    write_annotation_files(args.tier, selected, output_dir)
    print("\n標完後執行：")
    print(
        f"  python scripts/segmenter_v2_prepare_annotation.py --tier {args.tier} "
        f"--finalize {output_dir / f'{args.tier}_to_annotate.jsonl'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
