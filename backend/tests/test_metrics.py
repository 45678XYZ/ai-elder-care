"""EMF 指標測試。

重點在三件事：EMF 結構正確（否則 CloudWatch 解析不出指標）、維度基數受控
（session_id 這類高基數值不得成為維度）、以及指標可停用（測試與本機不該噴 EMF）。
"""

import json

import pytest

from src.shared import metrics


@pytest.fixture(autouse=True)
def enable_metrics(monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "true")


def emitted_lines(capsys):
    out = capsys.readouterr().out.strip()
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_emf_structure(capsys):
    metrics.emit({metrics.CHUNK_COUNT: 3}, dimensions={"ChunkerType": "llm_prompt"})
    payload = emitted_lines(capsys)[0]

    aws_block = payload["_aws"]
    assert isinstance(aws_block["Timestamp"], int)
    definition = aws_block["CloudWatchMetrics"][0]
    assert definition["Namespace"] == metrics.NAMESPACE
    assert definition["Dimensions"] == [["ChunkerType"]]
    assert definition["Metrics"] == [{"Name": metrics.CHUNK_COUNT, "Unit": "Count"}]
    # 維度值與指標值都要在同一層
    assert payload["ChunkerType"] == "llm_prompt"
    assert payload[metrics.CHUNK_COUNT] == 3.0


def test_units_are_declared_for_non_count_metrics():
    units = metrics.summarize_units(
        [metrics.MODEL_LATENCY, metrics.DEDUP_MERGE_RATE, metrics.EVENT_COUNT]
    )
    assert units[metrics.MODEL_LATENCY] == "Milliseconds"
    assert units[metrics.DEDUP_MERGE_RATE] == "Percent"
    assert units[metrics.EVENT_COUNT] == "Count"


def test_properties_are_not_dimensions(capsys):
    """session_id 是高基數值：只能進 properties，不能成為維度。"""
    metrics.emit(
        {metrics.EVENT_COUNT: 1},
        dimensions={"EventType": "medication"},
        properties={"session_id": "ses_1"},
    )
    payload = emitted_lines(capsys)[0]
    assert payload["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["EventType"]]
    assert payload["session_id"] == "ses_1"


def test_disabled_by_env(capsys, monkeypatch):
    monkeypatch.setenv("METRICS_ENABLED", "false")
    assert metrics.emit({metrics.EVENT_COUNT: 1}) is None
    assert capsys.readouterr().out == ""


def test_empty_values_are_skipped(capsys):
    assert metrics.emit({}) is None
    assert capsys.readouterr().out == ""


def test_pipeline_metrics_cover_plan_items(capsys):
    pipeline_metrics = {
        "chunk_count": 3,
        "event_count": 5,
        "dropped_events": 1,
        "unmatched_predicates": 2,
        "dedup_merge_rate": 0.25,
        "dedup_key_merged": 2,
        "dedup_alias_merged": 1,
        "chunker_fallback_used": True,
        "structured_output_degraded": 1,
        "model_latency_ms": 4200,
        "type_distribution": {"medication": 3, "safety": 2},
    }
    metrics.emit_pipeline_metrics(
        pipeline_metrics, elder_id="eld_1", session_id="ses_1", chunker_type="embedding_depth"
    )
    payloads = emitted_lines(capsys)

    main = payloads[0]
    assert main[metrics.CHUNK_COUNT] == 3
    assert main[metrics.EVENT_COUNT] == 5
    assert main[metrics.DROPPED_EVENTS] == 1
    assert main[metrics.UNMATCHED_PREDICATES] == 2
    # 比率以 Percent 呈現
    assert main[metrics.DEDUP_MERGE_RATE] == 25.0
    assert main[metrics.CHUNKER_FALLBACK] == 1
    assert main[metrics.STRUCTURED_OUTPUT_DEGRADED] == 1
    assert main[metrics.MODEL_LATENCY] == 4200
    assert main["ChunkerType"] == "embedding_depth"

    # 分類分佈一筆一筆送，維度是 EventType 才能分類別看趨勢
    by_type = {payload["EventType"]: payload[metrics.EVENTS_BY_TYPE] for payload in payloads[1:]}
    assert by_type == {"medication": 3.0, "safety": 2.0}


def test_batch_outcome_metrics(capsys):
    metrics.emit_batch_outcome("acquired", attempts=2, duration_ms=1500, session_id="ses_1")
    payload = emitted_lines(capsys)[0]
    assert payload["Outcome"] == "acquired"
    assert payload[metrics.BATCH_OUTCOME] == 1
    assert payload[metrics.BATCH_ATTEMPTS] == 2
    assert payload[metrics.BATCH_DURATION] == 1500


def test_batch_outcome_without_optional_fields(capsys):
    metrics.emit_batch_outcome("already_completed")
    payload = emitted_lines(capsys)[0]
    assert metrics.BATCH_ATTEMPTS not in payload
    assert metrics.BATCH_DURATION not in payload


def test_dlq_and_sweep_metrics(capsys):
    metrics.emit_dlq_outcome("converged", session_id="ses_1")
    metrics.emit_sweep_result({"idle_closed": 2, "pending_requeued": 0})
    payloads = emitted_lines(capsys)

    assert payloads[0]["Outcome"] == "converged"
    sweeps = {payload["SweepKind"]: payload[metrics.SESSION_SWEEP] for payload in payloads[1:]}
    # 0 也要送：持續為 0 才是健康，缺資料點看不出差別
    assert sweeps == {"idle_closed": 2.0, "pending_requeued": 0.0}
