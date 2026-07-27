"""對話切分的評測指標。

只回報邊界 P/R/F1 是不夠的：切分任務對「差一格」特別敏感，而 P/R/F1 把差一格算成
「一個假陽性加一個假陰性」，懲罰過重。因此同時算 Pk 與 WindowDiff（兩者都是誤差越低越好，
以滑動視窗比較兩個切分是否認為視窗兩端同段），這是切分文獻的標準做法。

另外提供 `boundary_prf` 的容錯版（`tolerance`），允許差一格算命中，用來觀察模型是「抓錯位置」
還是「完全沒抓到」——兩者的改善方向不同。
"""

from collections.abc import Sequence
from typing import Any


def segment_ids(boundaries: Sequence[int], turn_count: int) -> list[int]:
    """把邊界轉成每個 turn 的段落編號。"""
    starts = sorted({0, *[value for value in boundaries if 0 < value < turn_count]})
    ids: list[int] = []
    current = -1
    start_set = set(starts)
    for index in range(turn_count):
        if index in start_set:
            current += 1
        ids.append(current)
    return ids


def average_segment_length(boundaries: Sequence[int], turn_count: int) -> float:
    segments = len({0, *[value for value in boundaries if 0 < value < turn_count]})
    return turn_count / segments if segments else float(turn_count)


def _window_size(gold: Sequence[int], turn_count: int) -> int:
    """Pk／WindowDiff 的視窗大小：黃金切分平均段長的一半（文獻慣例）。"""
    return max(2, int(round(average_segment_length(gold, turn_count) / 2)))


def pk(gold: Sequence[int], predicted: Sequence[int], turn_count: int, *, window: int | None = None) -> float:
    """Pk：滑動視窗兩端「是否同段」的判斷不一致比例。"""
    if turn_count < 2:
        return 0.0
    size = window or _window_size(gold, turn_count)
    gold_ids = segment_ids(gold, turn_count)
    pred_ids = segment_ids(predicted, turn_count)

    comparisons = 0
    errors = 0
    for start in range(turn_count - size):
        end = start + size
        gold_same = gold_ids[start] == gold_ids[end]
        pred_same = pred_ids[start] == pred_ids[end]
        comparisons += 1
        if gold_same != pred_same:
            errors += 1
    return errors / comparisons if comparisons else 0.0


def window_diff(
    gold: Sequence[int], predicted: Sequence[int], turn_count: int, *, window: int | None = None
) -> float:
    """WindowDiff：比較視窗內的邊界數量差異，對「多切／少切」比 Pk 更敏感。"""
    if turn_count < 2:
        return 0.0
    size = window or _window_size(gold, turn_count)
    gold_set = {value for value in gold if 0 < value < turn_count}
    pred_set = {value for value in predicted if 0 < value < turn_count}

    comparisons = 0
    errors = 0
    for start in range(turn_count - size):
        end = start + size
        gold_count = sum(1 for value in gold_set if start < value <= end)
        pred_count = sum(1 for value in pred_set if start < value <= end)
        comparisons += 1
        if gold_count != pred_count:
            errors += 1
    return errors / comparisons if comparisons else 0.0


def boundary_prf(
    gold: Sequence[int],
    predicted: Sequence[int],
    turn_count: int,
    *,
    tolerance: int = 0,
) -> dict[str, float]:
    """邊界 precision／recall／F1。

    索引 0 不算邊界（每段對話必然從 0 開始，計入會虛增分數）。
    `tolerance > 0` 時允許差 n 格算命中。
    """
    gold_set = {value for value in gold if 0 < value < turn_count}
    pred_set = {value for value in predicted if 0 < value < turn_count}

    if tolerance <= 0:
        true_positive = len(gold_set & pred_set)
    else:
        matched_gold: set[int] = set()
        true_positive = 0
        for value in sorted(pred_set):
            candidates = [
                candidate
                for candidate in gold_set - matched_gold
                if abs(candidate - value) <= tolerance
            ]
            if candidates:
                matched_gold.add(min(candidates, key=lambda item: abs(item - value)))
                true_positive += 1

    precision = true_positive / len(pred_set) if pred_set else 0.0
    recall = true_positive / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "gold_boundaries": len(gold_set),
        "predicted_boundaries": len(pred_set),
    }


def evaluate_dialogue(
    gold: Sequence[int], predicted: Sequence[int], turn_count: int
) -> dict[str, Any]:
    return {
        "turn_count": turn_count,
        **boundary_prf(gold, predicted, turn_count),
        "f1_tolerance_1": boundary_prf(gold, predicted, turn_count, tolerance=1)["f1"],
        "pk": round(pk(gold, predicted, turn_count), 4),
        "window_diff": round(window_diff(gold, predicted, turn_count), 4),
    }


def aggregate(per_dialogue: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """彙總。

    P/R/F1 用 micro（把所有對話的邊界合起來算），避免短對話與長對話等權重；
    Pk／WindowDiff 用 macro 平均，因為它們本身已經是比例。
    """
    if not per_dialogue:
        return {}

    total_gold = sum(item["gold_boundaries"] for item in per_dialogue)
    total_pred = sum(item["predicted_boundaries"] for item in per_dialogue)
    # 由 precision 反推 TP，避免再帶一份原始集合
    total_tp = sum(round(item["precision"] * item["predicted_boundaries"]) for item in per_dialogue)

    precision = total_tp / total_pred if total_pred else 0.0
    recall = total_tp / total_gold if total_gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    count = len(per_dialogue)
    return {
        "dialogues": count,
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(f1, 4),
        "macro_f1_tolerance_1": round(sum(item["f1_tolerance_1"] for item in per_dialogue) / count, 4),
        "macro_pk": round(sum(item["pk"] for item in per_dialogue) / count, 4),
        "macro_window_diff": round(sum(item["window_diff"] for item in per_dialogue) / count, 4),
    }
