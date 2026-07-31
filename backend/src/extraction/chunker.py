"""對話主題分塊模組 (Dialogue Topic Chunker)。

提供將已結束對話輪次 (Frozen Session Turns) 切分為主題獨立、問答脈絡完整 (QA Pair Closure)
之主題對話塊 (Chunks)。架構規範與決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md` §6。

本模組設計目的與核心機制：
- **三種切分模式 (`CHUNKER_TYPE`)**：
  1. `llm_prompt`（預設）：採用 Refined EST 認知事件分割與問答閉環 (QA Pair Closure) 提示詞，配合 Bedrock Structured Outputs（固定 Schema，享有高快取命中率）。
  2. `embedding_depth`：基於相鄰 Turn 向量餘弦相似度，計算 TextTiling Depth Score 凹谷深度，配合自適應門檻 `mean + k * std` 切分。
  3. `pairwise_v2`：採用離線訓練之決策樹模型，於 Lambda 端進行純 Python 輕量化邊界推論。
- **問答閉環原則 (QA Pair Closure)**：劃分主題區塊時，強制將「發起新話題的提問輪」作為新區塊起點，確保提問與對應的回答（如「有啊」、「吃了」）留在同一區塊內，避免答非所問或語意脫節。
- **極致容錯與防護網 (`plan_boundaries`)**：無論採用哪種模式，邊界均需通過 `validate_boundaries`；若 LLM 呼叫或演算法失敗，自動降級退回固定 Turn 數之機械切分並標記 `fallback_used = True`。
- **去除過時寫死模型**：棄用舊版 TF-IDF 與硬編碼 `.pkl` 檔（免除綁死 MiniLM-384 向量座標系），改以介面注入 Embedder 並採統計自適應門檻，更換向量模型時毋需重先微調參數。
"""

from collections.abc import Sequence
from dataclasses import dataclass
import logging
import math
import statistics

from src.shared import bedrock

from .config import (
    CHUNKER_EMBEDDING_DEPTH,
    CHUNKER_FULL_SESSION,
    CHUNKER_LLM_PROMPT,
    CHUNKER_PAIRWISE_V2,
)

logger = logging.getLogger(__name__)

# 邊界輸出的固定 JSON Schema；結構永遠保持恆定，允許 Bedrock 長期快取結構約束語法 (Grammar Caching)
BOUNDARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cognitive_event_goals": {
            "type": "array",
            "description": "每個主題區塊的目標描述",
            "items": {"type": "string"},
        },
        "boundaries": {
            "type": "array",
            "description": "每個主題區塊的起始 turn index，必須包含 0 且遞增",
            "items": {"type": "integer"},
        },
    },
    "required": ["boundaries", "cognitive_event_goals"],
}

# 認知事件分割 (EST) 與問答閉環 (QA Pair Closure) 提示詞
REFINED_EST_PROMPT = """你是長者照護對話的認知與問答脈絡分析專家。
請依 EST 認知事件分割理論找出這段對話的主題轉折點。

【劃分原則】
1. 照護目標切換才算邊界：例如由「睡眠與休息」轉到「生理量測／用藥」或「飲食水分」。
2. 問答閉環（QA Pair Closure）：話題切換時，把「發起新話題的提問那一輪」當成新區塊的起點，
   讓提問與對應的回答留在同一個區塊內。嚴禁把提問留在前一區塊末端、回答放到下一區塊開頭，
   否則「有啊」「吃過了」這類回答會失去問題脈絡。
3. 同話題內的細節追問、寒暄與附和不算邊界。
4. `boundaries` 是各區塊的起始 turn index，必須包含 0、遞增、且都小於總輪數 {total_turns}。

【對話逐字稿】
{formatted_transcript}
"""

# 預設每塊的預期 Turn 數；當 LLM 或深度分數演算法異常時退回之機械切分預設粒度
DEFAULT_FALLBACK_SIZE = 4

# 自適應門檻之標準差倍數；數值越大門檻越高（切分出的區塊越少且越保守）
DEFAULT_DEPTH_K = 0.5

_MODE_HANDLERS = frozenset(
    {CHUNKER_LLM_PROMPT, CHUNKER_EMBEDDING_DEPTH, CHUNKER_PAIRWISE_V2, CHUNKER_FULL_SESSION}
)


class ChunkerError(ValueError):
    """對話分塊輸入格式不合規、模式未支援或設定錯誤時拋出之例外。"""


@dataclass(frozen=True)
class Turn:
    """單一對話輪次資料容器；僅包含主題分塊所必需之基本欄位。"""

    conversation_id: str
    speaker: str
    text: str
    created_at: str


@dataclass(frozen=True)
class BoundaryPlan:
    """對話主題劃分計畫與結果容器。

    `fallback_used` 欄位用於監控與觀測「有多少比例的 Session 降級使用機械切分」——
    若此比例異常偏高，代表模型回應格式不穩定或演算法門檻需調整。
    """

    boundaries: tuple[int, ...]
    strategy: str
    goals: tuple[str, ...] = ()
    fallback_used: bool = False
    scores: tuple[float, ...] = ()


def format_transcript(turns: Sequence[Turn]) -> str:
    """將對話輪次格式化為帶有索引 (Turn Index) 的文字逐字稿，供 LLM 準確回報邊界索引。"""
    return "\n".join(
        f"Turn {index} | {turn.speaker}：{turn.text}" for index, turn in enumerate(turns)
    )


def validate_boundaries(
    boundaries: Sequence[int],
    total_turns: int,
    *,
    min_turns: int = 0,
) -> tuple[int, ...]:
    """校驗並標準化劃分邊界索引清單。

    要求：必須為整數、涵蓋 [0, total_turns) 範圍、開頭包含 0、去除重複並嚴格遞增。
    `min_turns > 0` 提供最小區塊長度保底；在模型品質評測時應設為 0 以展現真實演算法切分能力。
    """
    if total_turns <= 0:
        raise ChunkerError("turn 數必須大於 0")

    cleaned = sorted(
        {
            int(value)
            for value in boundaries
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < total_turns
        }
    )
    if not cleaned or cleaned[0] != 0:
        cleaned = [0, *[value for value in cleaned if value != 0]]

    if min_turns > 0:
        spaced = [cleaned[0]]
        for value in cleaned[1:]:
            if value - spaced[-1] >= min_turns and total_turns - value >= min_turns:
                spaced.append(value)
        cleaned = spaced

    return tuple(cleaned)


def fallback_boundaries(total_turns: int, size: int = DEFAULT_FALLBACK_SIZE) -> tuple[int, ...]:
    """計算固定 Turn 數之機械切分邊界；作為全模式失敗時的最底層保底防護網。"""
    step = max(1, size)
    return tuple(range(0, total_turns, step))


def plan_boundaries(
    turns: Sequence[Turn],
    *,
    chunker_type: str = CHUNKER_LLM_PROMPT,
    embedder=None,
    client=None,
    min_turns: int = 0,
    fallback_size: int = DEFAULT_FALLBACK_SIZE,
    depth_k: float = DEFAULT_DEPTH_K,
    segmenter=None,
    model_id: str | None = None,
) -> BoundaryPlan:
    """依指定之分塊模式規劃主題邊界；捕捉任何運行例外並自動降級退回機械切分。"""
    if not turns:
        raise ChunkerError("frozen turns 為空，無法分塊")

    # 模式名稱錯誤屬於部署設定問題，應立即拋出例外而非默默降級掩蓋問題
    if chunker_type not in _MODE_HANDLERS:
        raise ChunkerError(f"未知的分塊模式：{chunker_type}")

    total = len(turns)
    if total == 1 or chunker_type == CHUNKER_FULL_SESSION:
        return BoundaryPlan(boundaries=(0,), strategy=chunker_type)

    try:
        if chunker_type == CHUNKER_LLM_PROMPT:
            plan = _llm_prompt_boundaries(turns, client=client, model_id=model_id)
        elif chunker_type == CHUNKER_EMBEDDING_DEPTH:
            plan = _embedding_depth_boundaries(turns, embedder=embedder, depth_k=depth_k)
        else:
            plan = _pairwise_v2_boundaries(turns, embedder=embedder, segmenter=segmenter)
    except Exception as exc:
        logger.warning("分塊失敗，退回機械切分：mode=%s reason=%s", chunker_type, exc)
        return BoundaryPlan(
            boundaries=validate_boundaries(fallback_boundaries(total, fallback_size), total),
            strategy=chunker_type,
            fallback_used=True,
        )

    validated = validate_boundaries(plan.boundaries, total, min_turns=min_turns)
    if len(validated) == 0:
        validated = (0,)
    return BoundaryPlan(
        boundaries=validated,
        strategy=plan.strategy,
        goals=plan.goals,
        fallback_used=plan.fallback_used,
        scores=plan.scores,
    )


def _llm_prompt_boundaries(
    turns: Sequence[Turn], *, client=None, model_id: str | None = None
) -> BoundaryPlan:
    """利用 Bedrock Structured Outputs 執行基於 EST 與 QA 閉環之 LLM 主題分塊。"""
    prompt = REFINED_EST_PROMPT.format(
        total_turns=len(turns), formatted_transcript=format_transcript(turns)
    )
    data, _ = bedrock.converse_json(
        prompt,
        BOUNDARY_SCHEMA,
        model_id=model_id,
        schema_name="TopicBoundaries",
        client=client,
    )
    raw = data.get("boundaries") or []
    if not isinstance(raw, list):
        raise ChunkerError("模型未回傳 boundaries 陣列")
    goals = tuple(str(goal) for goal in (data.get("cognitive_event_goals") or []))
    return BoundaryPlan(boundaries=tuple(raw), strategy=CHUNKER_LLM_PROMPT, goals=goals)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """計算兩向量之餘弦相似度。"""
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def depth_scores(similarities: Sequence[float]) -> tuple[float, ...]:
    """計算 TextTiling Depth Score (TextTiling 深度分數)。

    Depth 是指「從特定轉折點向左右兩側波峰爬升之落差和」；
    相較於直接設定相似度絕對低點，Depth 分數在全文相似度普遍偏高或偏低時更具穩健性。
    """
    scores: list[float] = []
    for index, value in enumerate(similarities):
        left = value
        cursor = index - 1
        while cursor >= 0 and similarities[cursor] >= left:
            left = similarities[cursor]
            cursor -= 1
        right = value
        cursor = index + 1
        while cursor < len(similarities) and similarities[cursor] >= right:
            right = similarities[cursor]
            cursor += 1
        scores.append((left - value) + (right - value))
    return tuple(scores)


def _embedding_depth_boundaries(
    turns: Sequence[Turn], *, embedder, depth_k: float
) -> BoundaryPlan:
    """基於對話輪次 Embedding 向量之 TextTiling 深度分數與自適應門檻分塊。"""
    if embedder is None:
        raise ChunkerError("embedding_depth 模式需要 embedder")

    vectors = embedder.embed_documents([turn.text for turn in turns])
    if len(vectors) != len(turns):
        raise ChunkerError("embedding 數量與 turn 數不一致")

    similarities = [_cosine(vectors[index], vectors[index + 1]) for index in range(len(turns) - 1)]
    scores = depth_scores(similarities)

    # 採用基於 mean + k * std 之自適應門檻；當更換 Embedding 模型時，絕對數值尺度會改變但分布位置相對穩定
    threshold = statistics.fmean(scores) + depth_k * (
        statistics.pstdev(scores) if len(scores) > 1 else 0.0
    )
    boundaries = [0]
    for index, score in enumerate(scores):
        if score > threshold:
            boundaries.append(index + 1)

    return BoundaryPlan(
        boundaries=tuple(boundaries),
        strategy=CHUNKER_EMBEDDING_DEPTH,
        scores=scores,
    )


def _pairwise_v2_boundaries(turns: Sequence[Turn], *, embedder, segmenter) -> BoundaryPlan:
    """基於離線訓練導出之決策樹 Artifact 進行有監督主題邊界推論。"""
    if segmenter is None:
        raise ChunkerError("pairwise_v2 模式需要已載入的 segmenter artifact")
    if embedder is None:
        raise ChunkerError("pairwise_v2 模式需要 embedder")

    probabilities = segmenter.predict_boundary_probabilities(turns, embedder)
    boundaries = [0]
    for index, probability in enumerate(probabilities):
        if probability >= segmenter.threshold:
            boundaries.append(index + 1)
    return BoundaryPlan(
        boundaries=tuple(boundaries),
        strategy=CHUNKER_PAIRWISE_V2,
        scores=tuple(probabilities),
    )
