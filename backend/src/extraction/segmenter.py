"""有監督主題邊界分塊模型 (`pairwise_v2`) 執行期純 Python 推論模組。

提供載入 JSON 格式導出之 Gradient Boosting 決策樹模型資產，對對話輪次縫隙進行主題邊界機率預測。
離線訓練資料策略、上線 Gate 與評測規範詳見 `docs/feature_events-extraction.md` §7。

本模組設計目的與核心機制：
- **純 JSON 導出與零 ML 套件依賴 (No `.pkl`)**：將訓練完成之決策樹導出為 JSON 結構。避開 Python Pickle 綁定特定 scikit-learn 版本與反序列化安全疑慮；Lambda 執行期零機器學習套件依賴，且資產可進行 Code Review。
- **維度無關之特徵表示法 (Dimension-Invariant Features)**：舊版特徵直接包含原始向量元素，導致模型綁死特定 Embedding 維度（如 MiniLM-384）。本模組採用 13 個維度無關之統計特徵 (`FEATURE_SPEC`)，更換向量模型時僅需重新提取特徵訓練，無需修改特徵規格。
- **對話內長度 z-score 正規化 (`_z_scores`)**：對對話內各輪次文字長度進行 z-score 計算，消除跨語系與不同 ASR 逐字稿的字數分佈偏差。
- **無資產時之安全降級 (`load_segmenter`)**：預設若找不到 `pairwise_v2.json` 則傳回 `None`，引導 `plan_boundaries` 退回機械切分並發出警告，防止缺少選配資產導致整個批次任務失敗。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import logging
import math
import statistics

from .chunker import Turn, depth_scores
from .config import SEGMENTER_ASSETS_DIR

logger = logging.getLogger(__name__)

# 決策樹模型資產 JSON 檔名
SEGMENTER_ARTIFACT_FILE = "pairwise_v2.json"

# 特徵規格清單；特徵順序必須與模型資產之 `feature_spec` 嚴格一致，載入時會自動進行雙向校驗
FEATURE_SPEC: tuple[str, ...] = (
    "adjacent_cosine",
    "depth_score",
    "window_cosine_k2",
    "window_cosine_k3",
    "cosine_percentile",
    "cosine_delta_prev",
    "cosine_delta_next",
    "position_ratio",
    "left_length_z",
    "right_length_z",
    "length_diff_z",
    "speaker_changed",
    "center_similarity_delta",
)


class SegmenterError(ValueError):
    """資產檔缺失、向量維度不相符或特徵規格不一致時拋出之例外。"""


@dataclass(frozen=True)
class PairwiseSegmenter:
    """純 Python 決策樹集合推論器模型。"""

    artifact_version: str
    embedding_model_id: str
    embedding_dim: int
    feature_spec: tuple[str, ...]
    threshold: float
    trees: tuple[dict[str, Any], ...]
    init_score: float = 0.0
    learning_rate: float = 1.0
    model_card: dict[str, Any] = field(default_factory=dict)

    def predict_boundary_probabilities(
        self, turns: Sequence[Turn], embedder
    ) -> tuple[float, ...]:
        """預測對話中每個相鄰縫隙為主題轉折邊界之機率（回傳清單長度為 turn 數 - 1）。

        強烈校驗傳入之 embedder 向量維度是否與資產紀錄相符，防止用錯誤的向量座標系推論導致亂猜。
        """
        if len(turns) < 2:
            return ()
        if getattr(embedder, "dimension", self.embedding_dim) != self.embedding_dim:
            # 換 embedding 模型即更換向量座標系，維度不符直接拒絕推論
            raise SegmenterError(
                f"embedding 維度與 artifact 不符：artifact={self.embedding_dim} "
                f"embedder={getattr(embedder, 'dimension', None)}"
            )

        vectors = embedder.embed_documents([turn.text for turn in turns])
        features = extract_features(turns, vectors)
        return tuple(self._predict_one(row) for row in features)

    def _predict_one(self, features: Sequence[float]) -> float:
        """走訪所有決策樹並透過 Sigmoid 函式轉譯為機率值。"""
        raw = self.init_score
        for tree in self.trees:
            raw += self.learning_rate * _walk_tree(tree, features)
        return 1.0 / (1.0 + math.exp(-raw))


def _walk_tree(node: dict[str, Any], features: Sequence[float]) -> float:
    """純 Python 遞迴走訪單一 JSON 決策樹節點。"""
    while "value" not in node:
        index = int(node["feature"])
        node = node["left"] if features[index] <= float(node["threshold"]) else node["right"]
    return float(node["value"])


def load_segmenter(
    assets_dir: Path | str | None = None,
    *,
    required: bool = False,
) -> PairwiseSegmenter | None:
    """載入決策樹模型 JSON 資產。

    預設 `required=False` 時若找不到檔案會傳回 `None`，允許上層 `plan_boundaries` 平滑降級至預設模式。
    """
    base = Path(assets_dir) if assets_dir is not None else SEGMENTER_ASSETS_DIR
    path = base / SEGMENTER_ARTIFACT_FILE
    if not path.is_file():
        if required:
            raise SegmenterError(f"分塊模型 artifact 缺失：{path}")
        logger.info("未提供 pairwise_v2 artifact，此模式不可用：%s", path)
        return None

    with path.open(encoding="utf-8") as fp:
        raw = json.load(fp)

    spec = tuple(raw.get("feature_spec") or ())
    if spec != FEATURE_SPEC:
        raise SegmenterError(
            "artifact 的 feature_spec 與程式不一致；重訓後請同步更新 FEATURE_SPEC"
        )
    trees = raw.get("trees")
    if not isinstance(trees, list) or not trees:
        raise SegmenterError("artifact 不含決策樹")

    return PairwiseSegmenter(
        artifact_version=str(raw.get("artifact_version") or ""),
        embedding_model_id=str(raw.get("embedding_model_id") or ""),
        embedding_dim=int(raw["embedding_dim"]),
        feature_spec=spec,
        threshold=float(raw.get("threshold", 0.5)),
        trees=tuple(trees),
        init_score=float(raw.get("init_score", 0.0)),
        learning_rate=float(raw.get("learning_rate", 1.0)),
        model_card=dict(raw.get("model_card") or {}),
    )


# -----------------------------------------------------------------------------
# 特徵提取邏輯
# -----------------------------------------------------------------------------


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """計算兩向量之餘弦相似度。"""
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _mean_vector(vectors: Sequence[Sequence[float]]) -> list[float]:
    """計算向量集合之平均向量（質心）。"""
    if not vectors:
        return []
    dimension = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)]


def _z_scores(values: Sequence[float]) -> list[float]:
    """計算對話內數值之 z-score。

    主要用於文字長度特徵正規化，使字數特徵能跨語言與跨 ASR 語料分佈進行轉移；當標準差為 0 時傳回全零陣列防範除以零。
    """
    if not values:
        return []
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    if stdev == 0:
        return [0.0] * len(values)
    return [(value - mean) / stdev for value in values]


def _percentile_rank(values: Sequence[float], value: float) -> float:
    """計算特定數值在序列中之百分位排名。"""
    if not values:
        return 0.0
    below = sum(1 for candidate in values if candidate < value)
    return below / len(values)


def extract_features(
    turns: Sequence[Turn], vectors: Sequence[Sequence[float]]
) -> list[list[float]]:
    """為每個相鄰縫隙提取 13 維度無關之統計特徵；輸出順序與 `FEATURE_SPEC` 嚴格一致。"""
    if len(turns) != len(vectors):
        raise SegmenterError("turn 數與向量數不一致")
    gaps = len(turns) - 1
    if gaps <= 0:
        return []

    similarities = [_cosine(vectors[index], vectors[index + 1]) for index in range(gaps)]
    depths = depth_scores(similarities)
    center = _mean_vector(vectors)
    center_similarities = [_cosine(vector, center) for vector in vectors]
    lengths = [float(len(turn.text)) for turn in turns]
    length_z = _z_scores(lengths)

    rows: list[list[float]] = []
    for index in range(gaps):
        left_window_2 = _mean_vector(vectors[max(0, index - 1) : index + 1])
        right_window_2 = _mean_vector(vectors[index + 1 : index + 3])
        left_window_3 = _mean_vector(vectors[max(0, index - 2) : index + 1])
        right_window_3 = _mean_vector(vectors[index + 1 : index + 4])

        rows.append(
            [
                similarities[index],
                depths[index],
                _cosine(left_window_2, right_window_2),
                _cosine(left_window_3, right_window_3),
                _percentile_rank(similarities, similarities[index]),
                similarities[index] - similarities[index - 1] if index > 0 else 0.0,
                similarities[index + 1] - similarities[index] if index + 1 < gaps else 0.0,
                (index + 1) / len(turns),
                length_z[index],
                length_z[index + 1],
                length_z[index] - length_z[index + 1],
                1.0 if turns[index].speaker != turns[index + 1].speaker else 0.0,
                center_similarities[index] - center_similarities[index + 1],
            ]
        )
    return rows
