"""slot 去重與 pipeline 編排測試。

對應 framework Verification：同 slot 同 subject+predicate 合併、跨 slot 不合併、
predicate alias fallback 合併、detail／structured_detail 取最完整、evidence 聯集、
同 snapshot 兩次跑產出相同 canonical key 集合。
"""

import json

import pytest

from src.extraction.canonical import canonical_event_key, event_id_for, load_predicate_lexicon
from src.extraction.chunker import Turn
from src.extraction.config import CHUNKER_EMBEDDING_DEPTH, ExtractionConfig
from src.extraction.dedup import deduplicate, merge_events
from src.extraction.models import CanonicalEvent
from src.extraction.pipeline import ExtractionPipeline
from src.extraction.retriever import ConceptChunk, ConceptRetriever
from src.extraction.taxonomy import load_taxonomy
from tests.conftest import FakeConverseClient, StubEmbeddingProvider

ELDER = "eld_a1b2c3d4e5f6"
SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"
SLOT_MINUTES = 30


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def lexicon():
    return load_predicate_lexicon()


def make_event(
    *,
    ts="2026-07-26T09:05:00.000+08:00",
    subject="長者",
    predicate="服用血壓藥",
    concept_id=SCHEDULED,
    detail="吃了血壓藥",
    structured=None,
    evidence=("cnv_001",),
    confidence=0.9,
    chunk_id="chk_a",
) -> CanonicalEvent:
    key = canonical_event_key(ts, subject, predicate, SLOT_MINUTES)
    return CanonicalEvent(
        elder_id=ELDER,
        event_id=event_id_for(ELDER, key),
        canonical_event_key=key,
        ts=ts,
        type="medication",
        concept_id=concept_id,
        taxonomy_version="uco-1.0.0",
        subject=subject,
        predicate=predicate,
        detail=detail,
        structured_detail=dict(structured or {}),
        confidence=confidence,
        session_id="ses_1",
        source_chunk_id=chunk_id,
        evidence_conversation_ids=tuple(evidence),
    )


# -- 去重 ---------------------------------------------------------------------


def test_same_slot_same_predicate_merges(lexicon):
    events = [
        make_event(ts="2026-07-26T09:05:00.000+08:00", evidence=("cnv_001",)),
        make_event(ts="2026-07-26T09:25:00.000+08:00", evidence=("cnv_004",)),
    ]
    merged, stats = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)

    assert len(merged) == 1
    assert stats.key_merged == 1
    assert stats.input_count == 2 and stats.output_count == 1
    assert stats.merge_rate == pytest.approx(0.5)
    # evidence 取聯集
    assert merged[0].evidence_conversation_ids == ("cnv_001", "cnv_004")


def test_different_slot_does_not_merge(lexicon):
    events = [
        make_event(ts="2026-07-26T09:05:00.000+08:00"),
        make_event(ts="2026-07-26T09:35:00.000+08:00"),
    ]
    merged, stats = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    assert len(merged) == 2
    assert stats.key_merged == 0


def test_different_subject_does_not_merge(lexicon):
    events = [make_event(), make_event(subject="陳志明")]
    merged, _ = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    assert len(merged) == 2


def test_predicate_drift_within_slot_merges_to_lexicon_canonical(lexicon):
    """同 Slot 內語義相似謂語進行 embedding 聚類合併。"""
    from tests.conftest import StubEmbeddingProvider
    v = [1.0] + [0.0] * 7
    embedder = StubEmbeddingProvider(vectors={"服用血壓藥": v, "服血壓藥": v})
    events = [
        make_event(predicate="服用血壓藥", evidence=("cnv_001",)),
        make_event(predicate="服血壓藥", evidence=("cnv_002",)),
    ]
    merged, stats = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon, embedder=embedder)

    assert len(merged) == 1
    assert stats.alias_merged == 1
    assert merged[0].evidence_conversation_ids == ("cnv_001", "cnv_002")


def test_predicate_drift_across_concepts_does_not_merge(lexicon):
    events = [
        make_event(predicate="服用血壓藥", concept_id=SCHEDULED),
        make_event(predicate="量血壓", concept_id=VITAL),
    ]
    merged, _ = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    assert len(merged) == 2


def test_merge_keeps_most_complete_detail_and_attributes(lexicon):
    sparse = make_event(detail="吃藥", structured={"medication_item": "血壓藥"}, confidence=0.5)
    rich = make_event(
        detail="早餐後服用血壓藥一顆",
        structured={"medication_item": "血壓藥", "pill_count": 1, "on_time_flag": True},
        confidence=0.9,
    )
    merged, _ = deduplicate([sparse, rich], slot_minutes=SLOT_MINUTES, lexicon=lexicon)

    assert merged[0].detail == "早餐後服用血壓藥一顆"
    assert merged[0].structured_detail["pill_count"] == 1
    assert merged[0].confidence == pytest.approx(0.9)


def test_merge_fills_missing_attributes_from_the_other_event(lexicon):
    first = make_event(structured={"medication_item": "血壓藥", "pill_count": 1, "dosage": "一顆"})
    second = make_event(structured={"on_time_flag": True})
    merged, _ = deduplicate([first, second], slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    assert merged[0].structured_detail["on_time_flag"] is True
    assert merged[0].structured_detail["pill_count"] == 1


def test_merge_prefers_earliest_source_chunk(lexicon):
    events = [make_event(chunk_id="chk_b"), make_event(chunk_id="chk_a")]
    merged, _ = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    # 初建來源要穩定，不能隨合併順序改變
    assert merged[0].source_chunk_id == "chk_a"


def test_dedup_output_is_ordered_and_deterministic(lexicon):
    events = [
        make_event(ts="2026-07-26T10:05:00.000+08:00", predicate="量血壓", concept_id=VITAL),
        make_event(ts="2026-07-26T09:05:00.000+08:00"),
    ]
    first, _ = deduplicate(events, slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    second, _ = deduplicate(list(reversed(events)), slot_minutes=SLOT_MINUTES, lexicon=lexicon)
    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert [event.ts for event in first] == sorted(event.ts for event in first)


def test_dedup_empty_input():
    merged, stats = deduplicate([], slot_minutes=SLOT_MINUTES)
    assert merged == ()
    assert stats.merge_rate == 0.0


def test_merge_events_is_symmetric():
    first = make_event(detail="短", structured={"a": 1}, evidence=("cnv_001",))
    second = make_event(detail="比較長的描述", structured={"b": 2}, evidence=("cnv_002",))
    left = merge_events(first, second)
    right = merge_events(second, first)
    assert left.detail == right.detail == "比較長的描述"
    assert left.evidence_conversation_ids == right.evidence_conversation_ids


# -- pipeline -----------------------------------------------------------------


def make_turns():
    scripts = [
        ("AI", "阿嬤早安，昨天睡得好嗎？"),
        ("長者", "睡得不錯，晚上沒醒來。"),
        ("AI", "早上有量血壓嗎？"),
        ("長者", "量了，135/85，血壓藥也吃了一顆。"),
        ("AI", "那很好，等等要不要去散步？"),
        ("長者", "好啊，剛剛已經在公園走了一圈。"),
    ]
    return tuple(
        Turn(
            conversation_id=f"cnv_{index:03d}",
            speaker=speaker,
            text=text,
            created_at=f"2026-07-26T09:{index * 3:02d}:00.000+08:00",
        )
        for index, (speaker, text) in enumerate(scripts)
    )


def classification_payload(chunk_id, concept_ids):
    return json.dumps(
        {
            "chunk_id": chunk_id,
            "identified_labels": [
                {"concept_id": concept_id, "confidence": 0.9} for concept_id in concept_ids
            ],
            "rationale": "測試",
        },
        ensure_ascii=False,
    )


def extraction_payload(chunk_id, events):
    return json.dumps({"chunk_id": chunk_id, "events": events}, ensure_ascii=False)


@pytest.fixture
def pipeline(taxonomy, lexicon):
    chunks = (
        ConceptChunk(f"{SCHEDULED}#def", SCHEDULED, "按時服藥", "definition", "按時服藥 吃藥", 3),
        ConceptChunk(f"{VITAL}#def", VITAL, "生理數據量測紀錄", "definition", "量血壓 血糖", 3),
    )
    embedder = StubEmbeddingProvider()
    retriever = ConceptRetriever(taxonomy, embedder, chunks=chunks, top_k=2)
    config = ExtractionConfig(chunker_type=CHUNKER_EMBEDDING_DEPTH, event_slot_minutes=SLOT_MINUTES)
    return ExtractionPipeline(
        config=config,
        taxonomy=taxonomy,
        lexicon=lexicon,
        retriever=retriever,
        embedder=embedder,
    )


def medication_payload():
    return {
        "event_index": 0,
        "concept_id": SCHEDULED,
        "subject": "我",
        "predicate": "吃血壓藥",
        "event_summary": "早上服用血壓藥一顆",
        "raw_temporal_expression": "早上",
        "observed_at": None,
        "confidence_score": 0.8,
        "medication_item": "血壓藥",
    }


def test_pipeline_produces_canonical_events(pipeline, taxonomy):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)

    # 每個 chunk：先分類再萃取，共兩次呼叫
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
    pipeline.client = FakeConverseClient(texts)

    result = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest)

    assert result.events
    event = result.events[0]
    assert event.type == "medication"
    assert event.concept_id == SCHEDULED
    # subject 與 predicate 都經 server-owned 正規化
    assert event.predicate == "吃血壓藥"
    assert event.ts.startswith("2026-07-26T08:00")
    assert event.taxonomy_version == taxonomy.taxonomy_version
    assert event.source_chunk_id in {chunk.chunk_id for chunk in manifest.chunks}
    assert event.evidence_conversation_ids
    assert event.extraction_track == "batch"


def test_pipeline_takes_min_confidence_and_keeps_the_other(pipeline):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
    pipeline.client = FakeConverseClient(texts)

    event = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest).events[0]
    assert event.confidence == pytest.approx(0.8)
    assert event.structured_detail["classification_confidence"] == pytest.approx(0.9)


def test_pipeline_is_deterministic_for_same_snapshot(pipeline):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)

    def run_once():
        texts = []
        for chunk in manifest.chunks:
            texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
            texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
        pipeline.client = FakeConverseClient(texts)
        return pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest)

    first, second = run_once(), run_once()
    assert {event.canonical_event_key for event in first.events} == {
        event.canonical_event_key for event in second.events
    }
    assert [event.event_id for event in first.events] == [
        event.event_id for event in second.events
    ]


def test_pipeline_skips_chunk_without_hits(pipeline, caplog):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    pipeline.client = FakeConverseClient(
        [classification_payload(chunk.chunk_id, []) for chunk in manifest.chunks]
    )
    with caplog.at_level("INFO"):
        result = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest)
    assert result.events == ()
    assert "無命中標籤" in caplog.text


def test_pipeline_drops_event_without_predicate(pipeline, caplog):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    payload = medication_payload()
    payload["predicate"] = "   "
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [payload]))
    pipeline.client = FakeConverseClient(texts)

    with caplog.at_level("WARNING"):
        result = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest)
    assert result.events == ()
    assert "缺少可用謂語" in caplog.text


def test_pipeline_marks_suspected_routine_without_completion_event(pipeline):
    """決策 C：batch 只標記，不寫 completion event、不改 routine。"""
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    pipeline.suspected_routine_lookup = lambda concept_id, predicate, ts: "rtn_001"
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
    pipeline.client = FakeConverseClient(texts)

    event = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest).events[0]
    assert event.structured_detail["suspected_routine_id"] == "rtn_001"
    # 一般事件的 canonical key，不是 ROUTINE# 前綴的 completion key
    assert not event.canonical_event_key.startswith("ROUTINE#")
    # completion event 的欄位一個都不出現
    item = event.to_event_item()
    assert "routine_id" not in item
    assert "routine_date" not in item
    assert "completed_by" not in item


def test_pipeline_metrics_cover_observability_items(pipeline):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
    pipeline.client = FakeConverseClient(texts)

    metrics = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest).metrics
    for key in (
        "chunk_count",
        "event_count",
        "dropped_events",
        "unmatched_predicates",
        "dedup_merge_rate",
        "type_distribution",
        "chunker_fallback_used",
    ):
        assert key in metrics


def test_event_item_shape_matches_db_contract(pipeline):
    turns = make_turns()
    manifest = pipeline.plan("ses_1", "snap_1", turns)
    texts = []
    for chunk in manifest.chunks:
        texts.append(classification_payload(chunk.chunk_id, [SCHEDULED]))
        texts.append(extraction_payload(chunk.chunk_id, [medication_payload()]))
    pipeline.client = FakeConverseClient(texts)

    item = pipeline.run(ELDER, "ses_1", "snap_1", turns, manifest=manifest).events[0].to_event_item()
    for required in (
        "elder_id",
        "event_id",
        "canonical_event_key",
        "ts",
        "type",
        "detail",
        "concept_id",
        "taxonomy_version",
        "extraction_track",
        "source",
    ):
        assert item[required]
    # 逐字稿不落地（決策 D）
    assert "context_snippet" not in item
    assert "evidence_span" not in item
