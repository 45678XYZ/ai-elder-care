"""有監督分塊模型（pairwise_v2）的推論與 artifact 契約測試。

artifact 由離線訓練導出，這裡用手工造的小 artifact 鎖住格式與推論行為（golden test），
讓日後的訓練腳本有明確的輸出契約可對。
"""

import json

import pytest

from src.extraction.chunker import Turn, plan_boundaries
from src.extraction.config import CHUNKER_PAIRWISE_V2
from src.extraction.segmenter import (
    FEATURE_SPEC,
    PairwiseSegmenter,
    SegmenterError,
    extract_features,
    load_segmenter,
)
from tests.conftest import StubEmbeddingProvider

DIMENSION = 8


def make_turns(count=6):
    return tuple(
        Turn(
            conversation_id=f"cnv_{index:03d}",
            speaker="AI" if index % 2 == 0 else "長者",
            text=f"{'吃藥' if index < 3 else '散步'}的第 {index} 句",
            created_at=f"2026-07-26T09:{index:02d}:00.000+08:00",
        )
        for index in range(count)
    )


def write_artifact(tmp_path, **overrides):
    """單棵樹：相鄰餘弦相似度低於門檻就判定為邊界。"""
    artifact = {
        "artifact_version": "pairwise-v2-test",
        "embedding_model_id": "stub-embedding",
        "embedding_dim": DIMENSION,
        "feature_spec": list(FEATURE_SPEC),
        "threshold": 0.5,
        "init_score": 0.0,
        "learning_rate": 1.0,
        "trees": [
            {
                "feature": FEATURE_SPEC.index("adjacent_cosine"),
                "threshold": 0.5,
                "left": {"value": 2.0},
                "right": {"value": -2.0},
            }
        ],
        "model_card": {
            "labels": "human-annotated (TIAGE / DialSeg711)",
            "text": "machine-translated en→zh-TW",
            "split": "GroupKFold by dialogue_id",
            "baselines": {"embedding_depth": 0.0, "every_3_turns": 0.0},
        },
    }
    artifact.update(overrides)
    path = tmp_path / "pairwise_v2.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    return tmp_path


# -- 特徵 ---------------------------------------------------------------------


def test_feature_count_matches_spec():
    turns = make_turns()
    vectors = StubEmbeddingProvider(DIMENSION).embed_documents([turn.text for turn in turns])
    rows = extract_features(turns, vectors)
    assert len(rows) == len(turns) - 1
    assert all(len(row) == len(FEATURE_SPEC) for row in rows)


def test_features_are_embedding_dimension_agnostic():
    """換 embedding 維度後特徵數不變，才不會像原版一樣把模型綁死座標系。"""
    turns = make_turns()
    for dimension in (8, 16, 64):
        vectors = StubEmbeddingProvider(dimension).embed_documents([turn.text for turn in turns])
        rows = extract_features(turns, vectors)
        assert all(len(row) == len(FEATURE_SPEC) for row in rows)


def test_length_features_are_normalized_within_dialogue():
    """長度用對話內 z-score，跨語言（機翻中文 vs 真實逐字稿）才可轉移。"""
    turns = (
        Turn("cnv_0", "AI", "短", "2026-07-26T09:00:00.000+08:00"),
        Turn("cnv_1", "長者", "很長很長很長很長很長的一句話", "2026-07-26T09:01:00.000+08:00"),
        Turn("cnv_2", "AI", "短", "2026-07-26T09:02:00.000+08:00"),
    )
    vectors = StubEmbeddingProvider(DIMENSION).embed_documents([turn.text for turn in turns])
    rows = extract_features(turns, vectors)
    left_index = FEATURE_SPEC.index("left_length_z")
    # z-score 有正有負且量級接近 1，不是原始字數
    assert abs(rows[0][left_index]) < 3
    assert rows[0][left_index] != len(turns[0].text)


def test_speaker_change_feature():
    turns = make_turns(3)
    vectors = StubEmbeddingProvider(DIMENSION).embed_documents([turn.text for turn in turns])
    rows = extract_features(turns, vectors)
    index = FEATURE_SPEC.index("speaker_changed")
    assert rows[0][index] == 1.0


def test_feature_mismatch_raises():
    with pytest.raises(SegmenterError, match="不一致"):
        extract_features(make_turns(3), [[0.0] * DIMENSION] * 2)


def test_single_turn_yields_no_features():
    assert extract_features(make_turns(1), [[0.0] * DIMENSION]) == []


# -- artifact 載入 -------------------------------------------------------------


def test_missing_artifact_returns_none_by_default(tmp_path):
    assert load_segmenter(tmp_path) is None
    with pytest.raises(SegmenterError, match="缺失"):
        load_segmenter(tmp_path, required=True)


def test_artifact_round_trip(tmp_path):
    segmenter = load_segmenter(write_artifact(tmp_path))
    assert isinstance(segmenter, PairwiseSegmenter)
    assert segmenter.embedding_dim == DIMENSION
    assert segmenter.threshold == 0.5
    assert segmenter.feature_spec == FEATURE_SPEC
    # model card 要能分行說明標籤與文本來源，這是比賽可信度敘事的關鍵
    assert "human-annotated" in segmenter.model_card["labels"]
    assert "machine-translated" in segmenter.model_card["text"]
    assert "baselines" in segmenter.model_card


def test_artifact_with_stale_feature_spec_is_rejected(tmp_path):
    directory = write_artifact(tmp_path, feature_spec=["adjacent_cosine"])
    with pytest.raises(SegmenterError, match="feature_spec"):
        load_segmenter(directory)


def test_artifact_without_trees_is_rejected(tmp_path):
    directory = write_artifact(tmp_path, trees=[])
    with pytest.raises(SegmenterError, match="決策樹"):
        load_segmenter(directory)


# -- 推論 ---------------------------------------------------------------------


def test_probabilities_follow_tree_decision(tmp_path):
    segmenter = load_segmenter(write_artifact(tmp_path))
    turns = make_turns()
    embedder = StubEmbeddingProvider(DIMENSION)
    probabilities = segmenter.predict_boundary_probabilities(turns, embedder)

    assert len(probabilities) == len(turns) - 1
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    # 這棵樹只看相鄰相似度：低相似度 → 高機率
    assert all(value in (pytest.approx(0.880797, abs=1e-5), pytest.approx(0.119203, abs=1e-5)) for value in probabilities)


def test_inference_is_deterministic(tmp_path):
    segmenter = load_segmenter(write_artifact(tmp_path))
    turns = make_turns()
    first = segmenter.predict_boundary_probabilities(turns, StubEmbeddingProvider(DIMENSION))
    second = segmenter.predict_boundary_probabilities(turns, StubEmbeddingProvider(DIMENSION))
    assert first == second


def test_dimension_mismatch_is_rejected(tmp_path):
    """artifact 綁定訓練時的 embedding 座標系；換模型必須重訓而非硬跑。"""
    segmenter = load_segmenter(write_artifact(tmp_path))
    with pytest.raises(SegmenterError, match="維度"):
        segmenter.predict_boundary_probabilities(make_turns(), StubEmbeddingProvider(16))


def test_single_turn_returns_empty(tmp_path):
    segmenter = load_segmenter(write_artifact(tmp_path))
    assert segmenter.predict_boundary_probabilities(make_turns(1), StubEmbeddingProvider(DIMENSION)) == ()


def test_plan_boundaries_uses_loaded_segmenter(tmp_path):
    segmenter = load_segmenter(write_artifact(tmp_path))
    turns = make_turns()
    plan = plan_boundaries(
        turns,
        chunker_type=CHUNKER_PAIRWISE_V2,
        embedder=StubEmbeddingProvider(DIMENSION),
        segmenter=segmenter,
    )
    assert plan.strategy == CHUNKER_PAIRWISE_V2
    assert plan.fallback_used is False
    assert plan.boundaries[0] == 0


def test_dimension_mismatch_degrades_to_fallback(tmp_path, caplog):
    """設定錯誤不該讓整個 batch 掛掉，但要留下告警。"""
    segmenter = load_segmenter(write_artifact(tmp_path))
    with caplog.at_level("WARNING"):
        plan = plan_boundaries(
            make_turns(),
            chunker_type=CHUNKER_PAIRWISE_V2,
            embedder=StubEmbeddingProvider(16),
            segmenter=segmenter,
        )
    assert plan.fallback_used is True
    assert "退回機械切分" in caplog.text
