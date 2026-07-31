"""動態 schema 組裝測試。

重點：屬性沿祖先鏈繼承、跨分類屬性不滲透、prompt 表示與驗證器同源、
輸出 schema 落在 Bedrock structured outputs 支援的子集內、以及不落地逐字稿欄位。
"""

import pytest
from pydantic import ValidationError

from src.extraction.models import LabelHit
from src.extraction.schema_composer import (
    EXCLUDED_GLOBAL_PROPERTIES,
    check_schema_constraints,
    compose_multi_event,
    describe_for_prompt,
    prune_irrelevant_event_properties,
)
from src.extraction.taxonomy import load_taxonomy

SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"
SLEEP_ONSET = "UCO.BehavioralRecord.SleepRestBehavior.SleepOnset"


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def composed(taxonomy):
    hits = [LabelHit(concept_id=SCHEDULED, confidence=0.9), LabelHit(concept_id=VITAL, confidence=0.8)]
    return compose_multi_event(hits, taxonomy)


def test_properties_inherit_along_ancestor_chain(composed):
    """葉節點自身屬性與其類別節點屬性都要收進來。"""
    scheduled_props = composed.properties_by_concept[SCHEDULED]
    # 類別層（MedicationBehavior）
    assert "medication_item" in scheduled_props
    assert "dosage" in scheduled_props
    # 葉節點層（ScheduledMedication）
    assert "on_time_flag" in scheduled_props
    assert "pill_count" in scheduled_props
    # 不屬於此鏈的屬性不得出現
    assert "systolic_bp" not in scheduled_props


def test_each_concept_keeps_its_own_whitelist(composed):
    vital_props = composed.properties_by_concept[VITAL]
    assert "systolic_bp" in vital_props
    assert "measurement_type" in vital_props
    assert "medication_item" not in vital_props


def test_model_accepts_valid_event(composed):
    payload = {
        "chunk_id": "chk_1",
        "reference_datetime": "2026-07-25T20:00:00.000+08:00",
        "events": [
            {
                "event_index": 0,
                "concept_id": SCHEDULED,
                "subject": "長者",
                "predicate": "服用血壓藥",
                "event_summary": "早餐後服用血壓藥一顆",
                "confidence_score": 0.92,
                "medication_item": "血壓藥",
                "pill_count": 1,
            }
        ],
    }
    parsed = composed.container_model.model_validate(payload)
    assert parsed.events[0].concept_id == SCHEDULED
    assert parsed.events[0].raw_temporal_expression is None


def test_model_rejects_unknown_concept_id(composed):
    """concept_id 以 Literal 收斂，幻覺標籤在驗證層就被擋下。"""
    with pytest.raises(ValidationError):
        composed.event_model.model_validate(
            {
                "event_index": 0,
                "concept_id": SLEEP_ONSET,
                "subject": "長者",
                "predicate": "入睡困難",
                "event_summary": "半夜睡不著",
                "confidence_score": 0.5,
            }
        )


def test_model_rejects_extra_fields(composed):
    """extra='forbid' 讓 schema 輸出 additionalProperties=false，也擋掉模型亂加欄位。"""
    with pytest.raises(ValidationError):
        composed.event_model.model_validate(
            {
                "event_index": 0,
                "concept_id": SCHEDULED,
                "subject": "長者",
                "predicate": "服用血壓藥",
                "event_summary": "吃藥",
                "confidence_score": 0.5,
                "made_up_field": "x",
            }
        )


def test_transcript_fields_are_not_in_schema(composed):
    """決策 D：逐字稿片段不落地，模型連填的欄位都不存在。"""
    assert "source_utterance" in EXCLUDED_GLOBAL_PROPERTIES
    assert "source_utterance" not in composed.event_model.model_fields
    assert "context_snippet" not in composed.event_model.model_fields
    assert "evidence_span" not in composed.event_model.model_fields
    assert "rationale" not in composed.event_model.model_fields


def test_canonical_identity_fields_exist(composed):
    """canonical key 需要 subject 與 predicate，缺一個就算不出事件身分。"""
    assert "subject" in composed.event_model.model_fields
    assert "predicate" in composed.event_model.model_fields
    with pytest.raises(ValidationError):
        composed.event_model.model_validate(
            {
                "event_index": 0,
                "concept_id": SCHEDULED,
                "event_summary": "吃藥",
                "confidence_score": 0.5,
            }
        )


def test_node_properties_are_never_required(composed):
    """節點專屬屬性必須可為 null，否則另一個 concept 的事件會永遠驗證失敗。"""
    required = {
        name for name, info in composed.event_model.model_fields.items() if info.is_required()
    }
    for concept_id, props in composed.properties_by_concept.items():
        assert required.isdisjoint(props), f"{concept_id} 的專屬屬性被設成必填"
    # 全域屬性仍可依 registry 的 nullable 設為必填
    assert "confidence_score" in required


def test_open_object_property_is_carried_as_string(composed):
    """開放式 object 不在 Bedrock schema 子集內，改以字串承載。"""
    field = composed.event_model.model_fields["severity_qualifier"]
    parsed = composed.event_model.model_validate(
        {
            "event_index": 0,
            "concept_id": SCHEDULED,
            "subject": "長者",
            "predicate": "服用血壓藥",
            "event_summary": "吃了血壓藥",
            "confidence_score": 0.9,
            "severity_qualifier": "2 中度",
        }
    )
    assert parsed.severity_qualifier == "2 中度"
    assert field.is_required() is False


def test_schema_stays_in_bedrock_supported_subset(composed):
    assert check_schema_constraints(composed.schema_json) == []


def test_check_schema_constraints_detects_violations():
    bad = {
        "type": "object",
        "additionalProperties": True,
        "properties": {"score": {"type": "number", "minimum": 0}},
    }
    violations = check_schema_constraints(bad)
    assert any("additionalProperties" in v for v in violations)
    assert any("minimum" in v for v in violations)


def test_fingerprint_is_stable_and_order_independent(taxonomy):
    first = compose_multi_event(
        [LabelHit(concept_id=SCHEDULED), LabelHit(concept_id=VITAL)], taxonomy
    )
    second = compose_multi_event(
        [LabelHit(concept_id=VITAL), LabelHit(concept_id=SCHEDULED)], taxonomy
    )
    third = compose_multi_event([LabelHit(concept_id=SCHEDULED)], taxonomy)
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != third.fingerprint


def test_prune_removes_cross_concept_properties(composed, caplog):
    """血壓值不得留在用藥事件上，即使有值也要剔除。"""
    event = {
        "event_index": 0,
        "concept_id": SCHEDULED,
        "subject": "長者",
        "predicate": "服用血壓藥",
        "event_summary": "吃了血壓藥",
        "confidence_score": 0.9,
        "medication_item": "血壓藥",
        "systolic_bp": 135,
        "measurement_type": None,
    }
    with caplog.at_level("WARNING"):
        cleaned = prune_irrelevant_event_properties(event, composed)
    assert "medication_item" in cleaned
    assert "systolic_bp" not in cleaned
    assert "measurement_type" not in cleaned
    assert "systolic_bp" in caplog.text


def test_prune_keeps_base_and_global_fields(composed):
    event = {
        "event_index": 1,
        "concept_id": VITAL,
        "subject": "長者",
        "predicate": "量血壓",
        "event_summary": "血壓 135/85",
        "raw_temporal_expression": "早上",
        "observed_at": "2026-07-25T08:00:00.000+08:00",
        "confidence_score": 0.8,
        "observer_role": "長者本人",
        "systolic_bp": 135,
        "diastolic_bp": 85,
    }
    cleaned = prune_irrelevant_event_properties(event, composed)
    assert cleaned == event


def test_prompt_description_and_validator_come_from_same_composition(composed, taxonomy):
    text = describe_for_prompt(composed, taxonomy)
    # 允許的節點清單與 Literal 一致
    for concept_id in composed.concept_ids:
        assert concept_id in text
    # 屬性白名單逐節點列出，這是 prompt 指引模式下屬性隔離的唯一防線
    assert "medication_item" in text
    assert "systolic_bp" in text
    assert "屬性隔離" in text
    # JSON Schema 全文附在 prompt 內
    assert '"additionalProperties": false' in text
    assert "null" in text


def test_prompt_description_includes_open_world_predicate_guidance(composed, taxonomy):
    text = describe_for_prompt(
        composed,
        taxonomy,
    )
    assert "predicate 填寫規則" in text
    assert "精簡的動詞短語" in text
    assert "__other__" not in text


def test_compose_requires_known_concept(taxonomy):
    with pytest.raises(ValueError):
        compose_multi_event([LabelHit(concept_id="UCO.NoSuchNode")], taxonomy)
