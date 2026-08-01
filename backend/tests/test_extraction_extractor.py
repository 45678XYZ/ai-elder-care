"""Single-pass 萃取器測試。"""

import json

import pytest

from src.extraction.canonical import load_predicate_lexicon
from src.extraction.config import EXTRACTION_STRUCTURED_OUTPUT
from src.extraction.extractor import (
    EXTRACTOR_VERSION,
    build_elder_context,
    build_extraction_prompt,
    extract_events,
)
from src.extraction.models import LabelHit
from src.extraction.schema_composer import compose_multi_event
from src.extraction.taxonomy import load_taxonomy
from src.shared import bedrock
from tests.conftest import FakeConverseClient

SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"
REFERENCE = "2026-07-26T09:41:23.456+08:00"
TRANSCRIPT = "AI：阿嬤早安。\n長者：早上量了血壓 135/85，血壓藥也吃了一顆。"

ELDER = {
    "name": "陳阿蘭",
    "nickname": "阿蘭嬤",
    "birth_year": 1948,
    "health_notes": ["高血壓", "膝關節退化"],
    "habit_note": "早睡早起",
    "address_region": "台北市大安區",
}


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def composed(taxonomy):
    hits = [LabelHit(concept_id=SCHEDULED, confidence=0.9), LabelHit(concept_id=VITAL, confidence=0.8)]
    return compose_multi_event(hits, taxonomy)


def medication_event(**overrides):
    event = {
        "event_index": 0,
        "concept_id": SCHEDULED,
        "subject": "長者",
        "predicate": "服用血壓藥",
        "event_summary": "早餐後服用血壓藥一顆",
        "raw_temporal_expression": "早上",
        "observed_at": "2026-07-26T08:00:00+08:00",
        "confidence_score": 0.92,
        "medication_item": "血壓藥",
        "pill_count": 1,
    }
    event.update(overrides)
    return event


def vital_event(**overrides):
    event = {
        "event_index": 1,
        "concept_id": VITAL,
        "subject": "長者",
        "predicate": "量血壓",
        "event_summary": "血壓 135/85",
        "raw_temporal_expression": "早上",
        "observed_at": "2026-07-26T08:00:00+08:00",
        "confidence_score": 0.88,
        "systolic_bp": 135.0,
        "diastolic_bp": 85.0,
    }
    event.update(overrides)
    return event


def payload(events):
    return json.dumps(
        {"chunk_id": "chk_1", "reference_datetime": REFERENCE, "events": events}, ensure_ascii=False
    )


def run(client, composed, taxonomy, **kwargs):
    return extract_events(
        "chk_1", TRANSCRIPT, REFERENCE, composed, taxonomy, client=client, **kwargs
    )


# -- prompt -------------------------------------------------------------------


def test_prompt_carries_dynamic_schema_rules(composed, taxonomy):
    prompt = build_extraction_prompt(
        "chk_1",
        TRANSCRIPT,
        REFERENCE,
        composed,
        taxonomy,
        elder=ELDER,
    )
    # 決策 H：不走硬約束，schema 規則必須在 prompt 裡明列
    assert "屬性隔離" in prompt
    assert "medication_item" in prompt
    assert "systolic_bp" in prompt
    assert '"additionalProperties": false' in prompt
    # 開放世界 predicate：不再列候選清單和 __other__ 出口
    assert "predicate 填寫規則" in prompt
    assert "__other__" not in prompt
    # 事實判定原則（P1 修復核心）
    assert "事實判定原則" in prompt
    # 時序推導基準與 chunk_id
    assert REFERENCE in prompt
    assert "chk_1" in prompt
    # canonical key 需要的兩個欄位要點名
    assert "subject" in prompt and "predicate" in prompt


def test_elder_context_is_included_but_minimal():
    context = build_elder_context(ELDER)
    assert "阿蘭嬤" in context
    assert "高血壓" in context
    # PII 最小化：地址對萃取無用，不進 prompt
    assert "大安區" not in context
    assert build_elder_context(None) == "（無長者背景資料）"
    assert build_elder_context({}) == "（無長者背景資料）"


# -- 正常路徑 -----------------------------------------------------------------


def test_extracts_multiple_events(composed, taxonomy):
    client = FakeConverseClient(payload([medication_event(), vital_event()]))
    result = run(client, composed, taxonomy)

    assert [event.concept_id for event in result.events] == [SCHEDULED, VITAL]
    assert result.dropped_events == 0
    assert result.metadata["extractor_version"] == EXTRACTOR_VERSION
    assert result.metadata["schema_fingerprint"] == composed.fingerprint
    assert result.metadata["raw_event_count"] == 2

    medication = result.events[0]
    assert medication.subject == "長者"
    assert medication.predicate == "服用血壓藥"
    assert medication.summary == "早餐後服用血壓藥一顆"
    assert medication.confidence == pytest.approx(0.92)
    assert medication.attributes["medication_item"] == "血壓藥"
    assert medication.attributes["pill_count"] == 1
    # confidence_score 提升為事件層欄位，不留在 structured_detail
    assert "confidence_score" not in medication.attributes
    # None 屬性不落地，避免 item 膨脹
    assert all(value is not None for value in medication.attributes.values())


def test_attributes_do_not_leak_across_concepts(composed, taxonomy, caplog):
    """血壓值被填進用藥事件時必須剔除，不能寫進 structured_detail。"""
    client = FakeConverseClient(payload([medication_event(systolic_bp=135.0)]))
    with caplog.at_level("WARNING"):
        result = run(client, composed, taxonomy)
    assert "systolic_bp" not in result.events[0].attributes
    assert "systolic_bp" in caplog.text


def test_missing_temporal_fields_are_allowed(composed, taxonomy):
    client = FakeConverseClient(
        payload([medication_event(raw_temporal_expression=None, observed_at=None)])
    )
    result = run(client, composed, taxonomy)
    assert result.events[0].observed_at is None
    assert result.events[0].raw_temporal_expression is None


def test_prompt_guided_mode_does_not_send_schema(composed, taxonomy):
    client = FakeConverseClient(payload([medication_event()]))
    run(client, composed, taxonomy)
    assert "outputConfig" not in client.requests[0]


def test_structured_output_mode_sends_schema(composed, taxonomy):
    client = FakeConverseClient(payload([medication_event()]))
    run(client, composed, taxonomy, extraction_mode=EXTRACTION_STRUCTURED_OUTPUT)
    assert client.requests[0]["outputConfig"]["textFormat"]["type"] == "json_schema"


# -- 失敗處理（決策 I）--------------------------------------------------------


def test_invalid_event_is_repaired_once(composed, taxonomy):
    broken = medication_event()
    broken.pop("predicate")
    client = FakeConverseClient(
        [payload([broken, vital_event()]), json.dumps({"events": [medication_event()]}, ensure_ascii=False)]
    )
    result = run(client, composed, taxonomy)

    assert result.dropped_events == 0
    assert {event.concept_id for event in result.events} == {SCHEDULED, VITAL}
    assert result.metadata["repair_attempts"] == 1
    # 修復請求只針對失敗的事件
    repair_prompt = client.requests[1]["messages"][0]["content"][0]["text"]
    assert "修復要求" in repair_prompt
    assert "predicate" in repair_prompt


def test_unrepairable_event_is_dropped_and_rest_kept(composed, taxonomy, caplog):
    broken = medication_event(concept_id="UCO.MadeUp.Node")
    client = FakeConverseClient([payload([broken, vital_event()]), json.dumps({"events": []})])
    with caplog.at_level("WARNING"):
        result = run(client, composed, taxonomy)

    assert result.dropped_events == 1
    assert [event.concept_id for event in result.events] == [VITAL]
    assert "事件驗證失敗已丟棄" in caplog.text


def test_repair_failure_does_not_break_chunk(composed, taxonomy, monkeypatch):
    from tests.conftest import client_error

    broken = medication_event()
    broken.pop("subject")
    client = FakeConverseClient(
        [payload([broken, vital_event()])],
        errors=[],
    )

    calls = {"count": 0}
    original = client.converse

    def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise client_error("AccessDeniedException")
        return original(**kwargs)

    monkeypatch.setattr(client, "converse", flaky)
    result = run(client, composed, taxonomy)
    assert result.dropped_events == 1
    assert [event.concept_id for event in result.events] == [VITAL]


def test_non_object_event_entries_are_dropped(composed, taxonomy):
    client = FakeConverseClient([payload(["壞掉的事件", vital_event()]), json.dumps({"events": []})])
    result = run(client, composed, taxonomy)
    assert result.dropped_events == 1
    assert [event.concept_id for event in result.events] == [VITAL]


def test_missing_events_array_yields_empty_result(composed, taxonomy, caplog):
    client = FakeConverseClient(json.dumps({"chunk_id": "chk_1"}))
    with caplog.at_level("WARNING"):
        result = run(client, composed, taxonomy)
    assert result.events == ()
    assert "缺少 events 陣列" in caplog.text


def test_unparseable_output_is_retryable(composed, taxonomy):
    """整份 JSON 解不開屬暫時性問題，交給上層重試而非丟掉整個 chunk。"""
    client = FakeConverseClient("我不想回 JSON")
    with pytest.raises(bedrock.RetryableBedrockError):
        run(client, composed, taxonomy)
