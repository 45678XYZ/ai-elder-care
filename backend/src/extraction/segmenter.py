"""有監督分塊模型（`pairwise_v2`）的執行期推論。

離線訓練在 `aws-hackathon`（資料策略與上線 gate 見 docs/feature_events-extraction.md §7），
這裡只負責把導出的純 Python artifact 載入並推論。三個設計選擇值得說明：

- **不載 `.pkl`**。sklearn 的 pickle 綁版本、反序列化本身也是風險面；改成把訓練好的
  GradientBoosting 導出為 JSON 決策樹，執行期零機器學習依賴、artifact 可 code review。
- **特徵與 embedding 維度無關**。原版特徵是 `[cos_sim, mean, max, std, abs_diff(D), dot_prod(D)]`，
  維度隨 embedding 模型改變，模型因此綁死某個座標系（這正是舊 MiniLM artifact 不能沿用的原因）。
  這裡改用約十來個尺度不變的統計量，換 embedding 模型只要重抽特徵重訓，feature spec 不用動。
- **長度類特徵做對話內正規化**。訓練文本是機翻的中文，與真實 ASR 逐字稿的字數分布不同；
  用對話內 z-score 讓這類特徵跨語言可轉移。

artifact 尚未 vendored：需要先在離線環境以 Bedrock embedding 重訓，並在人工標註的
Test-Real 上勝過「embedding_depth 無監督」與「每 3 輪機械切分」兩個基線才可設為預設。
在那之前 `CHUNKER_TYPE` 維持 `llm_prompt`／`embedding_depth`。
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

SEGMENTER_ARTIFACT_FILE = "pairwise_v2.json"

# 特徵順序即 artifact 的 feature_spec；載入時會比對，不一致直接拒絕
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
    """artifact 缺失或與程式的 feature spec 不一致。"""


@dataclass(frozen=True)
class PairwiseSegmenter:
    """決策樹集合的純 Python 推論器。"""

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
        """回傳每個相鄰縫隙是主題邊界的機率（長度為 turn 數 - 1）。"""
        if len(turns) < 2:
            return ()
        if getattr(embedder, "dimension", self.embedding_dim) != self.embedding_dim:
            # 換 embedding 模型就是換座標系，用舊模型推論等於亂猜
            raise SegmenterError(
                f"embedding 維度與 artifact 不符：artifact={self.embedding_dim} "
                f"embedder={getattr(embedder, 'dimension', None)}"
            )

        vectors = embedder.embed_documents([turn.text for turn in turns])
        features = extract_features(turns, vectors)
        return tuple(self._predict_one(row) for row in features)

    def _predict_one(self, features: Sequence[float]) -> float:
        raw = self.init_score
        for tree in self.trees:
            raw += self.learning_rate * _walk_tree(tree, features)
        return 1.0 / (1.0 + math.exp(-raw))


def _walk_tree(node: dict[str, Any], features: Sequence[float]) -> float:
    while "value" not in node:
        index = int(node["feature"])
        node = node["left"] if features[index] <= float(node["threshold"]) else node["right"]
    return float(node["value"])


def load_segmenter(
    assets_dir: Path | str | None = None,
    *,
    required: bool = False,
) -> PairwiseSegmenter | None:
    """載入 artifact。

    預設「找不到就回 None」：`pairwise_v2` 是選配模式，artifact 未 vendored 時
    `plan_boundaries` 會退回機械切分並告警，而不是讓整個 batch 失敗。
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
# 特徵
# -----------------------------------------------------------------------------


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _mean_vector(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    dimension = len(vectors[0])
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)]


def _z_scores(values: Sequence[float]) -> list[float]:
    """對話內 z-score；讓長度類特徵跨語言與跨語料可轉移。"""
    if not values:
        return []
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    if stdev == 0:
        return [0.0] * len(values)
    return [(value - mean) / stdev for value in values]


def _percentile_rank(values: Sequence[float], value: float) -> float:
    if not values:
        return 0.0
    below = sum(1 for candidate in values if candidate < value)
    return below / len(values)


def extract_features(
    turns: Sequence[Turn], vectors: Sequence[Sequence[float]]
) -> list[list[float]]:
    """為每個相鄰縫隙抽特徵；順序與 `FEATURE_SPEC` 一致。"""
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
