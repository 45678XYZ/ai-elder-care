"""語料載入與正規化。

所有語料統一成 `Dialogue`：turns 加上「邊界起始索引」。邊界標籤是**位置型**的（落在第 i 與
i+1 之間），這也是逐 turn 翻譯能安全沿用標籤的原因——文字換了、turn 數不變，位置就不變。

每份 Dialogue 都帶 `label_source` 與 `text_source`：model card 必須把「標籤來自真人」與
「文本經機器翻譯」分兩行寫清楚，混在一起講就守不住可信度。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from .contract import Turn

NEWLINE_TOKEN = "[NEWLINE]"
BOUNDARY_TOKEN = "[BOUNDARY]"

# 標籤來源
LABEL_HUMAN = "human-annotated"
LABEL_MECHANICAL = "mechanical-pseudo"

# 文本來源
TEXT_NATIVE = "native"
TEXT_MACHINE_TRANSLATED = "machine-translated"
TEXT_LLM_LOCALIZED = "llm-localized"
TEXT_LLM_GENERATED = "llm-generated"

_SPEAKER_MAP = {"user": "長者", "agent": "AI", "system": "AI"}


@dataclass
class Dialogue:
    """一段對話與其主題邊界。"""

    dialogue_id: str
    turns: list[Turn]
    boundaries: list[int]
    language: str = "en"
    label_source: str = LABEL_HUMAN
    text_source: str = TEXT_NATIVE
    corpus: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def to_json(self) -> dict[str, Any]:
        return {
            "dialogue_id": self.dialogue_id,
            "language": self.language,
            "label_source": self.label_source,
            "text_source": self.text_source,
            "corpus": self.corpus,
            "boundaries": list(self.boundaries),
            "turns": [
                {
                    "conversation_id": turn.conversation_id,
                    "speaker": turn.speaker,
                    "text": turn.text,
                    "created_at": turn.created_at,
                }
                for turn in self.turns
            ],
            "metadata": self.metadata,
        }


def make_turn(dialogue_id: str, index: int, speaker: str, text: str) -> Turn:
    """組出推論端相同形狀的 Turn。

    時間戳只是佔位（離線語料沒有真實時間），但仍給遞增值，讓任何依賴時間順序的邏輯
    行為與線上一致。
    """
    return Turn(
        conversation_id=f"{dialogue_id}_t{index:03d}",
        speaker=speaker,
        text=text,
        created_at=f"2026-01-01T00:{index % 60:02d}:00.000+08:00",
    )


def parse_dts_dialogue(dialogue_id: str, raw: str) -> tuple[list[Turn], list[int]]:
    """解析 Def-DTS 的 `dialogue` 字串。

    格式是 `[NEWLINE]` 串起的行，`[BOUNDARY]` 自成一行表示「下一行是新主題的起點」。
    回傳 `(turns, boundaries)`；boundaries 以 0 開頭，值為新主題的起始 turn index。
    """
    turns: list[Turn] = []
    boundaries: list[int] = [0]
    for line in raw.split(NEWLINE_TOKEN):
        line = line.strip()
        if not line:
            continue
        if line == BOUNDARY_TOKEN:
            # 邊界落在「已讀到的 turn 數」這個位置，也就是下一個 turn 的索引
            if turns and len(turns) not in boundaries:
                boundaries.append(len(turns))
            continue
        speaker, _, text = line.partition(":")
        text = text.strip()
        if not text:
            continue
        turns.append(
            make_turn(dialogue_id, len(turns), _SPEAKER_MAP.get(speaker.strip().lower(), speaker.strip()), text)
        )

    # 尾端的 [BOUNDARY] 會產生等於 turn 數的邊界，那不是有效起點
    boundaries = [value for value in boundaries if value < len(turns)]
    return turns, sorted(set(boundaries))


def load_dts_jsonl(
    path: Path | str,
    *,
    corpus: str = "",
    language: str = "en",
    text_source: str = TEXT_NATIVE,
    label_source: str = LABEL_HUMAN,
    limit: int | None = None,
) -> list[Dialogue]:
    """載入 Def-DTS 的 session dataset（tiage / dialseg711 / superseg）。"""
    resolved = Path(path)
    dialogues: list[Dialogue] = []
    with resolved.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            dialogue_id = item.get("id") or f"{resolved.stem}_{len(dialogues)}"
            turns, boundaries = parse_dts_dialogue(dialogue_id, item.get("dialogue", ""))
            if len(turns) < 2:
                continue
            dialogues.append(
                Dialogue(
                    dialogue_id=dialogue_id,
                    turns=turns,
                    boundaries=boundaries,
                    language=language,
                    label_source=label_source,
                    text_source=text_source,
                    corpus=corpus or resolved.stem,
                )
            )
            if limit is not None and len(dialogues) >= limit:
                break
    return dialogues


def save_dialogues(path: Path | str, dialogues: Iterable[Dialogue]) -> int:
    """存成本工作流的正規化格式。"""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with resolved.open("w", encoding="utf-8") as fp:
        for dialogue in dialogues:
            fp.write(json.dumps(dialogue.to_json(), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_dialogues(path: Path | str) -> list[Dialogue]:
    """讀回正規化格式。"""
    dialogues: list[Dialogue] = []
    with Path(path).open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            turns = [
                Turn(
                    conversation_id=turn["conversation_id"],
                    speaker=turn["speaker"],
                    text=turn["text"],
                    created_at=turn["created_at"],
                )
                for turn in item["turns"]
            ]
            dialogues.append(
                Dialogue(
                    dialogue_id=item["dialogue_id"],
                    turns=turns,
                    boundaries=list(item.get("boundaries") or [0]),
                    language=item.get("language", "en"),
                    label_source=item.get("label_source", LABEL_HUMAN),
                    text_source=item.get("text_source", TEXT_NATIVE),
                    corpus=item.get("corpus", ""),
                    metadata=item.get("metadata") or {},
                )
            )
    return dialogues


def gap_labels(dialogue: Dialogue) -> list[int]:
    """把邊界轉成每個相鄰縫隙的 0/1 標籤（長度為 turn 數 - 1）。"""
    boundary_set = set(dialogue.boundaries)
    return [1 if index + 1 in boundary_set else 0 for index in range(dialogue.turn_count - 1)]


def boundaries_from_gaps(gap_flags: Sequence[float], threshold: float = 0.5) -> list[int]:
    """把縫隙機率轉回邊界起始索引。"""
    boundaries = [0]
    for index, value in enumerate(gap_flags):
        if value >= threshold:
            boundaries.append(index + 1)
    return boundaries


def describe(dialogues: Sequence[Dialogue]) -> dict[str, Any]:
    """語料摘要；寫進 model card 用。"""
    total_gaps = sum(max(0, dialogue.turn_count - 1) for dialogue in dialogues)
    positives = sum(sum(gap_labels(dialogue)) for dialogue in dialogues)
    return {
        "dialogues": len(dialogues),
        "turns": sum(dialogue.turn_count for dialogue in dialogues),
        "gaps": total_gaps,
        "positive_gaps": positives,
        "positive_rate": round(positives / total_gaps, 4) if total_gaps else 0.0,
        "corpora": sorted({dialogue.corpus for dialogue in dialogues}),
        "languages": sorted({dialogue.language for dialogue in dialogues}),
        "label_sources": sorted({dialogue.label_source for dialogue in dialogues}),
        "text_sources": sorted({dialogue.text_source for dialogue in dialogues}),
    }
