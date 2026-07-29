"""把 sklearn 的 GradientBoosting 導出成純 Python artifact。

執行期不裝 sklearn，也不用 pickle：pickle 綁 sklearn 版本、反序列化本身是風險面，而且無法
code review。導出成決策樹 JSON 之後，Lambda 端只需要一段十幾行的走樹邏輯。

導出後會**自我驗證**：用同一批樣本比對 sklearn 的 `decision_function` 與 artifact 的原始分數，
偏差超過容忍值就拒絕輸出。這道檢查很便宜，但能擋掉「導出公式與 sklearn 內部實作不一致」
這種只有上線後才會發現的錯。
"""

from collections.abc import Sequence
from typing import Any
import math

MAX_EXPORT_DEVIATION = 1e-6


class ExportError(RuntimeError):
    """導出的 artifact 與 sklearn 模型行為不一致。"""


def _tree_to_dict(tree, node: int = 0) -> dict[str, Any]:
    """遞迴轉換 sklearn 的 `tree_` 陣列。"""
    if tree.children_left[node] == -1:
        return {"value": float(tree.value[node][0][0])}
    return {
        "feature": int(tree.feature[node]),
        "threshold": float(tree.threshold[node]),
        "left": _tree_to_dict(tree, int(tree.children_left[node])),
        "right": _tree_to_dict(tree, int(tree.children_right[node])),
    }


def _walk(node: dict[str, Any], features: Sequence[float]) -> float:
    while "value" not in node:
        node = node["left"] if features[int(node["feature"])] <= float(node["threshold"]) else node["right"]
    return float(node["value"])


def export_gradient_boosting(
    model,
    features: Sequence[Sequence[float]],
    *,
    feature_spec: Sequence[str],
    embedding_model_id: str,
    embedding_dim: int,
    threshold: float,
    artifact_version: str,
    model_card: dict[str, Any],
) -> dict[str, Any]:
    """導出 artifact；同時驗證分數一致。"""
    trees = [_tree_to_dict(estimator[0].tree_) for estimator in model.estimators_]
    learning_rate = float(model.learning_rate)

    # init 分數（先驗 log-odds）不從 sklearn 內部欄位取——那些欄位在版本間會變動。
    # 改用「decision_function 減去樹的貢獻」反推，並在下面驗證其一致性。
    sample = list(features[: min(len(features), 256)])
    if not sample:
        raise ExportError("導出需要至少一筆特徵樣本以驗證分數")

    raw_from_sklearn = model.decision_function(sample)
    tree_sums = [sum(_walk(tree, row) for tree in trees) for row in sample]
    init_candidates = [
        float(raw) - learning_rate * tree_sum
        for raw, tree_sum in zip(raw_from_sklearn, tree_sums)
    ]
    init_score = sum(init_candidates) / len(init_candidates)

    deviation = max(
        abs(float(raw) - (init_score + learning_rate * tree_sum))
        for raw, tree_sum in zip(raw_from_sklearn, tree_sums)
    )
    if deviation > MAX_EXPORT_DEVIATION:
        raise ExportError(
            f"導出的 artifact 與 sklearn 分數不一致（最大偏差 {deviation:.3e}）；"
            "請檢查是否使用了非預設的 loss 或 init estimator"
        )

    return {
        "artifact_version": artifact_version,
        "embedding_model_id": embedding_model_id,
        "embedding_dim": embedding_dim,
        "feature_spec": list(feature_spec),
        "threshold": float(threshold),
        "init_score": init_score,
        "learning_rate": learning_rate,
        "trees": trees,
        "model_card": model_card,
    }


def probability(artifact: dict[str, Any], features: Sequence[float]) -> float:
    """用 artifact 直接算機率；供導出後的抽查與 golden test 產生期望值。"""
    raw = float(artifact["init_score"])
    for tree in artifact["trees"]:
        raw += float(artifact["learning_rate"]) * _walk(tree, features)
    return 1.0 / (1.0 + math.exp(-raw))
