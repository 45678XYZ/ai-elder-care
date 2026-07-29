"""步驟 2：把英文語料逐 turn 翻成繁中，標籤位置沿用。

    python scripts/segmenter_v2_translate.py --input data/segmenter_v2/train_en.jsonl \
        --output data/segmenter_v2/train_zh.jsonl

為什麼這樣做安全：切分的標籤是**位置型**的（落在第 i 與 i+1 之間），不是 span 型。
逐 turn 翻譯時文字換了、turn 數不變，標籤位置原封不動，不需要重新標註。

為什麼逐 turn 翻而不是整段丟給模型：整段翻譯要靠模型乖乖不動 `[BOUNDARY]` 標記，
標籤完整性變成「希望模型配合」；逐 turn 翻譯再由程式重組，完整性是 by construction 的。
代價是丟失上下文，因此把前兩輪當 context 給模型看，但只翻目標那一輪。

翻譯後一律標記 `text_source=machine-translated`，`label_source` 維持 `human-annotated`：
model card 必須分兩行寫清楚，混在一起講就守不住可信度。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import corpora  # noqa: E402

# 翻譯走執行期同一份 Bedrock 呼叫層，避免另接一套 SDK 與重試語意
from src.shared import bedrock  # noqa: E402

TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"text": {"type": "string", "description": "翻譯後的繁體中文句子"}},
    "required": ["text"],
}

SYSTEM_PROMPT = (
    "你是英翻繁體中文的口語翻譯專家。只翻譯指定的那一句，"
    "保持口語自然、長度相當，不要加註解、不要合併或拆分句子。"
)


def build_prompt(context_lines: list[str], target: str) -> str:
    context_block = "\n".join(context_lines) if context_lines else "（無前文）"
    return f"""請把【待翻譯句】翻成台灣口語的繁體中文。

【前文（僅供理解代詞與省略，不要翻譯）】
{context_block}

【待翻譯句】
{target}
"""


def translate_dialogue(dialogue, *, client, model_id, context_turns=2, retries=2):
    translated = []
    for index, turn in enumerate(dialogue.turns):
        context_lines = [
            f"{item.speaker}：{item.text}"
            for item in dialogue.turns[max(0, index - context_turns) : index]
        ]
        prompt = build_prompt(context_lines, turn.text)
        text = ""
        for attempt in range(retries + 1):
            try:
                data, _ = bedrock.converse_json(
                    prompt,
                    TRANSLATION_SCHEMA,
                    system=SYSTEM_PROMPT,
                    model_id=model_id,
                    schema_name="Translation",
                    max_tokens=1024,
                    client=client,
                )
                text = str(data.get("text") or "").strip()
                if text:
                    break
            except bedrock.BedrockError as exc:
                if attempt >= retries:
                    raise
                print(f"    翻譯重試（{exc}）")
                time.sleep(2)
        if not text:
            # 翻不出來就保留原文並標記，稽核腳本會抓到殘留英文比例過高的對話
            text = turn.text
        translated.append(
            corpora.make_turn(f"{dialogue.dialogue_id}_zh", index, turn.speaker, text)
        )

    return corpora.Dialogue(
        dialogue_id=f"{dialogue.dialogue_id}_zh",
        turns=translated,
        boundaries=list(dialogue.boundaries),
        language="zh-TW",
        # 標籤來自真人、只有文本被翻譯，這個區別要守住
        label_source=dialogue.label_source,
        text_source=corpora.TEXT_MACHINE_TRANSLATED,
        corpus=dialogue.corpus,
        metadata={"source_dialogue_id": dialogue.dialogue_id},
    )


def audit(source, translated) -> list[str]:
    """機械可驗的稽核；語意品質仍建議人工抽 20 段掃過。"""
    problems: list[str] = []
    if len(source.turns) != len(translated.turns):
        problems.append("turn 數不一致")
    if list(source.boundaries) != list(translated.boundaries):
        problems.append("邊界位置不一致")
    for index, turn in enumerate(translated.turns):
        if not turn.text.strip():
            problems.append(f"turn {index} 翻譯為空")
        ascii_letters = sum(1 for char in turn.text if char.isascii() and char.isalpha())
        if turn.text and ascii_letters / len(turn.text) > 0.3:
            problems.append(f"turn {index} 殘留英文比例過高")
        if "```" in turn.text:
            problems.append(f"turn {index} 殘留程式碼圍欄")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="逐 turn 翻譯語料")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="", help="留空使用 shared.bedrock 的預設模型")
    parser.add_argument("--limit", type=int, default=None, help="只翻前 N 段（先抽查再全跑）")
    parser.add_argument("--context-turns", type=int, default=2)
    args = parser.parse_args(argv[1:])

    dialogues = corpora.load_dialogues(args.input)
    if args.limit:
        dialogues = dialogues[: args.limit]
    print(f"待翻譯：{len(dialogues)} 段 / {sum(d.turn_count for d in dialogues)} turns")

    output_path = Path(args.output)
    done_ids = set()
    if output_path.is_file():
        # 支援中斷續跑：已翻好的不重跑，省錢也省時間
        done_ids = {item.metadata.get("source_dialogue_id") for item in corpora.load_dialogues(output_path)}
        print(f"已完成 {len(done_ids)} 段，續跑剩下的")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit_problems: dict[str, list[str]] = {}
    with output_path.open("a", encoding="utf-8") as fp:
        for index, dialogue in enumerate(dialogues, start=1):
            if dialogue.dialogue_id in done_ids:
                continue
            translated = translate_dialogue(
                dialogue,
                client=None,
                model_id=args.model or None,
                context_turns=args.context_turns,
            )
            problems = audit(dialogue, translated)
            if problems:
                audit_problems[dialogue.dialogue_id] = problems
            fp.write(json.dumps(translated.to_json(), ensure_ascii=False) + "\n")
            fp.flush()
            if index % 20 == 0:
                print(f"  進度 {index}/{len(dialogues)}")

    print(f"\n稽核有問題的對話：{len(audit_problems)}")
    for dialogue_id, problems in list(audit_problems.items())[:10]:
        print(f"  {dialogue_id}: {problems}")
    print("提醒：機械稽核只驗形式；建議人工抽 20 段確認翻譯語感。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
