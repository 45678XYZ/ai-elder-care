"""分塊器與 chunk planner 測試。

對應 framework 的 Verification：core ranges 完整不重疊、每 turn 恰好一次、
context-only 不 emit、retry／duplicate／DLQ replay 重用同一份 manifest 與 chunk IDs、
非法 boundaries 安全 fallback、`min_turns` 保底可關閉。
"""

import json

import pytest

from src.extraction.chunk_planner import (
    ChunkPlanError,
    chunk_id_for,
    core_turn_ids,
    manifest_from_entries,
    plan_chunks,
    reference_datetime_for,
    render_chunk_text,
    validate_manifest,
)
from src.extraction.chunker import (
    BOUNDARY_SCHEMA,
    ChunkerError,
    Turn,
    depth_scores,
    fallback_boundaries,
    format_transcript,
    plan_boundaries,
    validate_boundaries,
)
from src.extraction.config import (
    CHUNKER_EMBEDDING_DEPTH,
    CHUNKER_LLM_PROMPT,
    CHUNKER_PAIRWISE_V2,
)
from src.extraction.schema_composer import check_schema_constraints
from tests.conftest import FakeConverseClient, StubEmbeddingProvider

PLANNER_VERSION = "chunk-planner-1"
SNAPSHOT = "snap_abc123"


def make_turns(count: int = 10) -> tuple[Turn, ...]:
    topics = ["睡覺", "吃藥", "血壓", "散步", "心情"]
    turns = []
    for index in range(count):
        topic = topics[(index // 2) % len(topics)]
        speaker = "AI" if index % 2 == 0 else "長者"
        turns.append(
            Turn(
                conversation_id=f"cnv_{index:03d}",
                speaker=speaker,
                text=f"{topic}的第 {index} 句",
                created_at=f"2026-07-26T09:{index:02d}:00.000+08:00",
            )
        )
    return tuple(turns)


# -- boundaries 驗證 -----------------------------------------------------------


def test_validate_boundaries_normalizes():
    assert validate_boundaries([3, 0, 3, 6], 10) == (0, 3, 6)
    # 缺 0 時補上；超界與非整數剔除
    assert validate_boundaries([3, 20, -1, "x", True], 10) == (0, 3)


def test_validate_boundaries_min_turns_can_be_disabled():
    boundaries = [0, 1, 2, 5]
    assert validate_boundaries(boundaries, 10, min_turns=0) == (0, 1, 2, 5)
    # 保底規則會吃掉過短的區塊，評測時要能關掉才分得清模型能力
    assert validate_boundaries(boundaries, 10, min_turns=3) == (0, 5)


def test_validate_boundaries_rejects_empty_dialogue():
    with pytest.raises(ChunkerError):
        validate_boundaries([0], 0)


def test_fallback_boundaries():
    assert fallback_boundaries(10, 4) == (0, 4, 8)
    assert fallback_boundaries(3, 0) == (0, 1, 2)


# -- llm_prompt 模式 -----------------------------------------------------------


def test_boundary_schema_is_bedrock_compatible():
    assert check_schema_constraints(BOUNDARY_SCHEMA) == []


def test_llm_prompt_mode_uses_fixed_schema():
    turns = make_turns()
    client = FakeConverseClient(
        json.dumps({"boundaries": [0, 4, 8], "cognitive_event_goals": ["A", "B", "C"]})
    )
    plan = plan_boundaries(turns, chunker_type=CHUNKER_LLM_PROMPT, client=client)

    assert plan.boundaries == (0, 4, 8)
    assert plan.goals == ("A", "B", "C")
    assert plan.fallback_used is False
    request = client.requests[0]
    assert request["outputConfig"]["textFormat"]["jsonSchema"]["name"] == "TopicBoundaries"
    prompt = request["messages"][0]["content"][0]["text"]
    # QA pair closure 是這個 prompt 的核心原則，漏掉會讓「有啊」失去問句脈絡
    assert "問答閉環" in prompt
    assert "Turn 0 |" in prompt


def test_llm_prompt_illegal_boundaries_are_cleaned():
    turns = make_turns()
    client = FakeConverseClient(
        json.dumps({"boundaries": [5, 99, -3, 2], "cognitive_event_goals": []})
    )
    plan = plan_boundaries(turns, chunker_type=CHUNKER_LLM_PROMPT, client=client)
    assert plan.boundaries == (0, 2, 5)


def test_llm_failure_falls_back_to_mechanical_split(caplog):
    turns = make_turns()
    client = FakeConverseClient("完全不是 JSON")
    with caplog.at_level("WARNING"):
        plan = plan_boundaries(turns, chunker_type=CHUNKER_LLM_PROMPT, client=client)
    assert plan.fallback_used is True
    assert plan.boundaries == (0, 4, 8)
    assert "退回機械切分" in caplog.text


# -- embedding_depth 模式 ------------------------------------------------------


def test_depth_scores_peak_at_topic_change():
    similarities = [0.9, 0.9, 0.1, 0.9, 0.9]
    scores = depth_scores(similarities)
    assert scores[2] == pytest.approx(1.6)
    assert max(scores) == scores[2]


def test_embedding_depth_mode_finds_boundaries():
    turns = make_turns()
    plan = plan_boundaries(
        turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=StubEmbeddingProvider()
    )
    assert plan.boundaries[0] == 0
    assert plan.fallback_used is False
    assert len(plan.scores) == len(turns) - 1
    assert all(0 <= boundary < len(turns) for boundary in plan.boundaries)


def test_embedding_depth_is_deterministic():
    """同一份 snapshot 重跑必須得到同一組邊界，否則 chunk_id 會漂移。"""
    turns = make_turns()
    first = plan_boundaries(
        turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=StubEmbeddingProvider()
    )
    second = plan_boundaries(
        turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=StubEmbeddingProvider()
    )
    assert first.boundaries == second.boundaries


def test_embedding_depth_threshold_is_adaptive():
    """k 越大越保守；門檻是相對量而非絕對值，換 embedding 模型不用重調。"""
    turns = make_turns(12)
    loose = plan_boundaries(
        turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=StubEmbeddingProvider(), depth_k=0.0
    )
    strict = plan_boundaries(
        turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=StubEmbeddingProvider(), depth_k=3.0
    )
    assert len(loose.boundaries) >= len(strict.boundaries)


def test_embedding_depth_requires_embedder():
    turns = make_turns()
    plan = plan_boundaries(turns, chunker_type=CHUNKER_EMBEDDING_DEPTH, embedder=None)
    # 缺 embedder 屬設定錯誤，但不能讓整個 batch 掛掉
    assert plan.fallback_used is True


# -- pairwise_v2 模式 ----------------------------------------------------------


class StubSegmenter:
    threshold = 0.5

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_boundary_probabilities(self, turns, embedder):
        return self.probabilities


def test_pairwise_v2_uses_segmenter_probabilities():
    turns = make_turns(5)
    plan = plan_boundaries(
        turns,
        chunker_type=CHUNKER_PAIRWISE_V2,
        embedder=StubEmbeddingProvider(),
        segmenter=StubSegmenter([0.1, 0.9, 0.2, 0.8]),
    )
    assert plan.boundaries == (0, 2, 4)
    assert plan.strategy == CHUNKER_PAIRWISE_V2


def test_pairwise_v2_without_artifact_falls_back(caplog):
    """artifact 缺失時退回機械切分並告警，不安靜地改用別的模式。"""
    turns = make_turns()
    with caplog.at_level("WARNING"):
        plan = plan_boundaries(
            turns, chunker_type=CHUNKER_PAIRWISE_V2, embedder=StubEmbeddingProvider(), segmenter=None
        )
    assert plan.fallback_used is True
    assert "退回機械切分" in caplog.text


def test_unknown_chunker_type_raises():
    with pytest.raises(ChunkerError, match="未知的分塊模式"):
        plan_boundaries(make_turns(), chunker_type="magic")


def test_empty_turns_raises():
    with pytest.raises(ChunkerError, match="為空"):
        plan_boundaries([], chunker_type=CHUNKER_LLM_PROMPT)


def test_single_turn_session():
    plan = plan_boundaries(make_turns(1), chunker_type=CHUNKER_LLM_PROMPT)
    assert plan.boundaries == (0,)


# -- chunk planner -------------------------------------------------------------


def test_core_ranges_partition_all_turns_exactly_once():
    turns = make_turns()
    manifest = plan_chunks(
        "ses_1", SNAPSHOT, turns, (0, 4, 8), planner_version=PLANNER_VERSION
    )
    covered = [
        index
        for chunk in manifest.chunks
        for index in range(chunk.core_start, chunk.core_end + 1)
    ]
    assert sorted(covered) == list(range(len(turns)))
    assert [chunk.core_turn_count for chunk in manifest.chunks] == [4, 4, 2]
    assert [chunk.ordinal for chunk in manifest.chunks] == [0, 1, 2]


def test_context_overlap_extends_beyond_core_but_not_past_ends():
    turns = make_turns()
    manifest = plan_chunks(
        "ses_1", SNAPSHOT, turns, (0, 4, 8), planner_version=PLANNER_VERSION, context_overlap=2
    )
    first, middle, last = manifest.chunks
    assert (first.context_start, first.context_end) == (0, 5)
    assert (middle.context_start, middle.context_end) == (2, 9)
    assert (last.context_start, last.context_end) == (6, 9)


def test_context_only_turns_are_marked_and_excluded_from_evidence():
    turns = make_turns()
    manifest = plan_chunks(
        "ses_1", SNAPSHOT, turns, (0, 4, 8), planner_version=PLANNER_VERSION
    )
    middle = manifest.chunks[1]
    text = render_chunk_text(turns, middle)
    assert "（脈絡）" in text
    ids = core_turn_ids(turns, middle)
    assert ids == tuple(f"cnv_{index:03d}" for index in range(4, 8))
    # context turn 不進 evidence
    assert "cnv_003" not in ids
    assert "cnv_008" not in ids


def test_chunk_id_is_stable_and_scoped_to_snapshot():
    turns = make_turns()
    first = plan_chunks("ses_1", SNAPSHOT, turns, (0, 4, 8), planner_version=PLANNER_VERSION)
    same = plan_chunks("ses_1", SNAPSHOT, turns, (0, 4, 8), planner_version=PLANNER_VERSION)
    other_snapshot = plan_chunks(
        "ses_1", "snap_other", turns, (0, 4, 8), planner_version=PLANNER_VERSION
    )

    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in same.chunks]
    assert [c.chunk_id for c in first.chunks] != [c.chunk_id for c in other_snapshot.chunks]
    assert all(chunk.chunk_id.startswith("chk_") for chunk in first.chunks)
    # ordinal 參與雜湊：邊界相同但序號不同的 chunk 不會撞 ID
    assert chunk_id_for(SNAPSHOT, "cnv_000", "cnv_003", 0) != chunk_id_for(
        SNAPSHOT, "cnv_000", "cnv_003", 1
    )


def test_manifest_round_trip_reuses_same_chunk_ids():
    """retry／duplicate delivery／DLQ replay 都走還原路徑，不重新分塊。"""
    turns = make_turns()
    manifest = plan_chunks("ses_1", SNAPSHOT, turns, (0, 3, 7), planner_version=PLANNER_VERSION)
    entries = manifest.to_manifest()

    restored = manifest_from_entries("ses_1", SNAPSHOT, PLANNER_VERSION, list(reversed(entries)))
    assert [c.chunk_id for c in restored.chunks] == [c.chunk_id for c in manifest.chunks]
    assert [c.core_start for c in restored.chunks] == [c.core_start for c in manifest.chunks]
    validate_manifest(restored, len(turns))


def test_manifest_entries_are_compact_metadata_only():
    turns = make_turns()
    entry = plan_chunks("ses_1", SNAPSHOT, turns, (0, 5), planner_version=PLANNER_VERSION).to_manifest()[0]
    assert set(entry) == {
        "chunk_id",
        "ordinal",
        "core_start",
        "core_end",
        "context_start",
        "context_end",
        "first_core_turn_id",
        "last_core_turn_id",
    }
    # manifest 存在 session item 內，不得夾帶逐字稿
    assert "睡覺" not in json.dumps(entry, ensure_ascii=False)


def test_plan_chunks_rejects_bad_boundaries():
    turns = make_turns()
    with pytest.raises(ChunkPlanError, match="以 0 開始"):
        plan_chunks("ses_1", SNAPSHOT, turns, (2, 5), planner_version=PLANNER_VERSION)
    with pytest.raises(ChunkPlanError):
        plan_chunks("ses_1", SNAPSHOT, [], (0,), planner_version=PLANNER_VERSION)


def test_validate_manifest_detects_gaps_and_overlaps():
    turns = make_turns(6)
    manifest = plan_chunks("ses_1", SNAPSHOT, turns, (0, 3), planner_version=PLANNER_VERSION)

    broken_overlap = manifest.__class__(
        session_id="ses_1",
        session_snapshot_hash=SNAPSHOT,
        planner_version=PLANNER_VERSION,
        chunks=(manifest.chunks[0], manifest.chunks[0]),
    )
    with pytest.raises(ChunkPlanError):
        validate_manifest(broken_overlap, len(turns))

    broken_gap = manifest.__class__(
        session_id="ses_1",
        session_snapshot_hash=SNAPSHOT,
        planner_version=PLANNER_VERSION,
        chunks=(manifest.chunks[0],),
    )
    with pytest.raises(ChunkPlanError, match="未完整覆蓋"):
        validate_manifest(broken_gap, len(turns))


def test_reference_datetime_comes_from_last_core_turn():
    turns = make_turns()
    manifest = plan_chunks("ses_1", SNAPSHOT, turns, (0, 4), planner_version=PLANNER_VERSION)
    assert reference_datetime_for(turns, manifest.chunks[0]) == turns[3].created_at
    assert reference_datetime_for(turns, manifest.chunks[1]) == turns[-1].created_at


def test_format_transcript_includes_turn_index():
    text = format_transcript(make_turns(2))
    assert text.startswith("Turn 0 | AI：")
    assert "Turn 1 | 長者：" in text
