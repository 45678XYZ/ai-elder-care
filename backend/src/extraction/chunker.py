"""對話分塊：把 closed session 的 frozen turns 切成主題邊界。

三種模式（`CHUNKER_TYPE` 切換）：

| 模式 | 作法 | 執行期依賴 |
|---|---|---|
| `llm_prompt`（預設） | 移植 hackathon 的 Refined EST + QA Pair Closure prompt，走 Bedrock structured outputs（schema 固定，grammar 快取命中率最高） | Bedrock |
| `embedding_depth` | 每 turn 取 embedding，算相鄰餘弦相似度的 TextTiling depth score，門檻用自適應 `mean + k·std` | Bedrock embedding |
| `pairwise_v2` | 離線訓練的決策樹 artifact，Lambda 端純 Python 推論 | 無（artifact 內含） |

不移植任何 `.pkl`：原 TF-IDF 版對中文退化成固定 3 輪機械切分，新多語言版又把特徵綁死
MiniLM-384 座標系（理由詳見 docs/feature_events-extraction.md §6）。因此 embedder 一律
以介面注入，門檻用自適應式而非絕對值，換 embedding 模型不需重調參數。

無論哪個模式，邊界都必須通過 `validate_boundaries`；不通過就退回固定 turn 數切分。
framework 允許 chunk planner 非確定性（首次 manifest 條件式持久化後重用），所以用 embedding
或 LLM 都不破壞冪等。
"""

from collections.abc import Sequence
from dataclasses import dataclass
import logging
import math
import statistics

from src.shared import bedrock

from .config import (
    CHUNKER_EMBEDDING_DEPTH,
    CHUNKER_LLM_PROMPT,
    CHUNKER_PAIRWISE_V2,
)

logger = logging.getLogger(__name__)

# 邊界輸出的固定 schema；形狀永不變，grammar 可長期快取
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

# 預設每塊的目標 turn 數；LLM 或 depth score 失效時的機械切分粒度
DEFAULT_FALLBACK_SIZE = 4

# 自適應門檻的標準差倍數；越大越保守（切得越少）
DEFAULT_DEPTH_K = 0.5


_MODE_HANDLERS = frozenset({CHUNKER_LLM_PROMPT, CHUNKER_EMBEDDING_DEPTH, CHUNKER_PAIRWISE_V2})


class ChunkerError(ValueError):
    """分塊輸入或設定不合法（模式名稱錯誤、turns 為空）。"""


@dataclass(frozen=True)
class Turn:
    """frozen session 的單一 turn（分塊只需要這幾個欄位）。"""

    conversation_id: str
    speaker: str
    text: str
    created_at: str


@dataclass(frozen=True)
class BoundaryPlan:
    """分塊結果。

    `fallback_used` 讓上層能觀測「有多少 session 其實是機械切分」——這個比例若偏高，
    代表模型或門檻需要調整，而不是安靜地當成正常。
    """

    boundaries: tuple[int, ...]
    strategy: str
    goals: tuple[str, ...] = ()
    fallback_used: bool = False
    scores: tuple[float, ...] = ()


def format_transcript(turns: Sequence[Turn]) -> str:
    """帶 turn index 的逐字稿；LLM 要靠 index 回報邊界。"""
    return "\n".join(
        f"Turn {index} | {turn.speaker}：{turn.text}" for index, turn in enumerate(turns)
    )


def validate_boundaries(
    boundaries: Sequence[int],
    total_turns: int,
    *,
    min_turns: int = 0,
) -> tuple[int, ...]:
    """驗證並正規化邊界。

    要求：整數、落在 `[0, total_turns)`、含 0、去重遞增。`min_turns > 0` 時額外要求
    每個區塊至少該長度——這是保底規則，評測時要能關掉（設 0），否則分不清是模型好
    還是保底規則剛好對。
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
    """固定 turn 數的機械切分；所有模式失敗時的保底。"""
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
    """依設定的模式規劃主題邊界；任何失敗都退回機械切分。"""
    if not turns:
        raise ChunkerError("frozen turns 為空，無法分塊")

    # 模式名稱寫錯是部署設定問題，不該被 fallback 掩蓋
    if chunker_type not in _MODE_HANDLERS:
        raise ChunkerError(f"未知的分塊模式：{chunker_type}")

    total = len(turns)
    if total == 1:
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
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def depth_scores(similarities: Sequence[float]) -> tuple[float, ...]:
    """TextTiling depth score。

    depth 是「從這個縫隙往左右各爬到區域高點的落差之和」，比單純的相似度低點穩健：
    整段對話相似度普遍偏低時不會到處都切，普遍偏高時也還抓得到轉折。
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
    if embedder is None:
        raise ChunkerError("embedding_depth 模式需要 embedder")

    vectors = embedder.embed_documents([turn.text for turn in turns])
    if len(vectors) != len(turns):
        raise ChunkerError("embedding 數量與 turn 數不一致")

    similarities = [_cosine(vectors[index], vectors[index + 1]) for index in range(len(turns) - 1)]
    scores = depth_scores(similarities)

    # 自適應門檻：換 embedding 模型時相似度的絕對尺度會變，但分布位置不變
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
    """有監督分塊；模型 artifact 由離線訓練導出成純 Python 資產。

    artifact 缺失時視為失敗，由 `plan_boundaries` 退回機械切分並告警——這比安靜地
    改用別的模式好，因為那會讓上線 gate 的判定失去意義。
    """
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
