"""上線 gate 用的兩個基線。

`pairwise_v2` 必須在人工標註的繁中測試集上**同時勝過**這兩個基線才可設為預設：

- `every_n_turns`：機械切分。放它是為了證明模型不是退化——舊 TF-IDF 版看起來有 90% 準確率，
  實際上退化成固定每 3 輪切一次，這個基線能一眼看出來。
- `embedding_depth`：無監督 TextTiling depth score，也就是本專案不需要訓練就能上線的路徑。
  有監督模型若贏不過它，就沒有理由承擔訓練與 artifact 維運的成本。
"""

from collections.abc import Sequence
import math
import statistics

from .contract import Turn, depth_scores


def every_n_turns(turn_count: int, n: int = 3) -> list[int]:
    """每 n 輪切一次。"""
    step = max(1, n)
    return list(range(0, turn_count, step))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def embedding_depth(
    vectors: Sequence[Sequence[float]],
    *,
    depth_k: float = 0.5,
) -> list[int]:
    """無監督 depth score 切分；與執行期 `embedding_depth` 模式同一套演算法與門檻。"""
    if len(vectors) < 2:
        return [0]
    similarities = [_cosine(vectors[index], vectors[index + 1]) for index in range(len(vectors) - 1)]
    scores = depth_scores(similarities)
    threshold = statistics.fmean(scores) + depth_k * (
        statistics.pstdev(scores) if len(scores) > 1 else 0.0
    )
    boundaries = [0]
    for index, score in enumerate(scores):
        if score > threshold:
            boundaries.append(index + 1)
    return boundaries


def depth_score_gaps(vectors: Sequence[Sequence[float]]) -> list[float]:
    """回傳每個縫隙的 depth score，供調門檻與畫圖。"""
    if len(vectors) < 2:
        return []
    similarities = [_cosine(vectors[index], vectors[index + 1]) for index in range(len(vectors) - 1)]
    return list(depth_scores(similarities))


def turn_texts(turns: Sequence[Turn]) -> list[str]:
    return [turn.text for turn in turns]
