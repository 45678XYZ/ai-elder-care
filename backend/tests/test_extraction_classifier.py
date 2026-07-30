"""RAC 分類器測試（以假 client 驗證，不打網路）。"""

import json

import pytest

from src.extraction.classifier import (
    CLASSIFIER_VERSION,
    build_classification_prompt,
    build_classification_schema,
    candidates_from_taxonomy,
    classify_chunk,
)
from src.extraction.schema_composer import check_schema_constraints
from src.extraction.taxonomy import load_taxonomy
from tests.conftest import FakeConverseClient

SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
MEDICATION = "UCO.BehavioralRecord.MedicationBehavior"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"

TRANSCRIPT = "AI：阿嬤早安。\n長者：我早上量了血壓，135/85，藥也吃了。"


@pytest.fixture
def candidates():
    taxonomy = load_taxonomy()
    return candidates_from_taxonomy(taxonomy, [SCHEDULED, MEDICATION, VITAL])


def response_text(labels, rationale="依對話內容判斷"):
    return json.dumps(
        {"chunk_id": "chk_1", "identified_labels": labels, "rationale": rationale},
        ensure_ascii=False,
    )


def test_schema_constrains_concept_id_to_candidates(candidates):
    schema = build_classification_schema([c.concept_id for c in candidates])
    enum = schema["properties"]["identified_labels"]["items"]["properties"]["concept_id"]["enum"]
    assert enum == [SCHEDULED, MEDICATION, VITAL]
    # 不索取原文片段（決策 D）
    assert "evidence_span" not in json.dumps(schema)
    assert check_schema_constraints(schema) == []


def test_prompt_contains_candidate_definitions_and_chunk_id(candidates):
    prompt = build_classification_prompt("chk_1", TRANSCRIPT, candidates)
    for candidate in candidates:
        assert candidate.concept_id in prompt
        assert candidate.display_name in prompt
    assert "chk_1" in prompt
    assert TRANSCRIPT in prompt
    # 類別節點退守規則要寫進 prompt，否則細節不足的對話會整段漏標
    assert "退守" in prompt


def test_classify_returns_sorted_hits(candidates):
    client = FakeConverseClient(
        response_text(
            [
                {"concept_id": VITAL, "confidence": 0.7},
                {"concept_id": SCHEDULED, "confidence": 0.95},
            ]
        )
    )
    result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)

    assert [hit.concept_id for hit in result.hits] == [SCHEDULED, VITAL]
    assert result.hits[0].display_name == "按時服藥"
    assert result.rationale == "依對話內容判斷"
    assert result.metadata["classifier_version"] == CLASSIFIER_VERSION
    assert result.metadata["candidate_count"] == 3


def test_hallucinated_labels_are_dropped(candidates, caplog):
    """降級路徑沒有 grammar 約束，候選集外的標籤必須在後處理擋掉。"""
    client = FakeConverseClient(
        response_text(
            [
                {"concept_id": "UCO.MadeUp.Thing", "confidence": 0.9},
                {"concept_id": SCHEDULED, "confidence": 0.9},
            ]
        )
    )
    with caplog.at_level("WARNING"):
        result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)
    assert [hit.concept_id for hit in result.hits] == [SCHEDULED]
    assert "候選集外" in caplog.text


def test_low_confidence_hits_are_filtered(candidates):
    client = FakeConverseClient(
        response_text([{"concept_id": SCHEDULED, "confidence": 0.1}, {"concept_id": VITAL, "confidence": 0.8}])
    )
    result = classify_chunk("chk_1", TRANSCRIPT, candidates, min_confidence=0.3, client=client)
    assert [hit.concept_id for hit in result.hits] == [VITAL]


def test_confidence_is_clamped_and_non_numeric_dropped(candidates, caplog):
    client = FakeConverseClient(
        response_text(
            [
                {"concept_id": SCHEDULED, "confidence": 1.7},
                {"concept_id": VITAL, "confidence": "高"},
            ]
        )
    )
    with caplog.at_level("WARNING"):
        result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)
    assert len(result.hits) == 1
    assert result.hits[0].confidence == 1.0
    assert "非數值" in caplog.text


def test_duplicate_labels_keep_highest_confidence(candidates):
    client = FakeConverseClient(
        response_text([{"concept_id": SCHEDULED, "confidence": 0.4}, {"concept_id": SCHEDULED, "confidence": 0.9}])
    )
    result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)
    assert len(result.hits) == 1
    assert result.hits[0].confidence == pytest.approx(0.9)


def test_no_hits_is_valid(candidates):
    client = FakeConverseClient(response_text([], rationale="對話只有寒暄"))
    result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)
    assert result.hits == ()


def test_empty_candidates_skips_model_call():
    client = FakeConverseClient(response_text([]))
    result = classify_chunk("chk_1", TRANSCRIPT, (), client=client)
    assert result.hits == ()
    assert result.metadata["skipped"] == "no_candidates"
    assert client.requests == []


def test_malformed_label_entries_are_ignored(candidates):
    client = FakeConverseClient(
        json.dumps(
            {
                "chunk_id": "chk_1",
                "identified_labels": ["不是物件", {"confidence": 0.9}, {"concept_id": SCHEDULED, "confidence": 0.9}],
                "rationale": "",
            },
            ensure_ascii=False,
        )
    )
    result = classify_chunk("chk_1", TRANSCRIPT, candidates, client=client)
    assert [hit.concept_id for hit in result.hits] == [SCHEDULED]


def test_candidates_from_taxonomy_skips_unknown_nodes():
    taxonomy = load_taxonomy()
    candidates = candidates_from_taxonomy(taxonomy, [SCHEDULED, "UCO.NotReal"])
    assert [candidate.concept_id for candidate in candidates] == [SCHEDULED]
    assert candidates[0].definition
