"""摘要生成邏輯測試。

鎖住 docs/feature_daily-summarization.md §6 的分工：可計算的事實一律由程式算，模型只寫
overview／sections／alerts；以及 api.md 的 `data_status`、七類 sections 與空資料日行為。
"""

import json
from unittest.mock import patch

import pytest

from src.shared import summarizer
from src.shared.models import SUMMARY_SECTION_KEYS

from tests.conftest import FakeConverseClient

ELDER = "eld_a1b2c3d4e5f6"
DATE = "2026-07-26"
CUTOFF = "2026-07-26T20:00:00.000+08:00"

MODEL_OUTPUT = json.dumps(
    {
        "overview": "三餐正常，按時服藥，下午到公園散步。",
        "sections": {
            "diet": "三餐正常",
            "activity": "下午到公園散步約 30 分鐘",
            "sleep": None,
            "medication": "血壓藥已按時服用",
            "wellbeing": "提到膝蓋疼痛",
            "safety": None,
            "other": None,
        },
        "alerts": ["今日多次提到膝蓋疼痛"],
    },
    ensure_ascii=False,
)

DAY_EVENTS = [
    {
        "event_id": "evt_2",
        "ts": "2026-07-26T14:30:00.000+08:00",
        "type": "wellbeing",
        "detail": "提到膝蓋疼痛，語氣低落",
    },
    {
        "event_id": "evt_1",
        "ts": "2026-07-26T09:05:00.000+08:00",
        "type": "medication",
        "detail": "早餐後服用血壓藥一顆",
        "structured_detail": {"medication_item": "血壓藥", "pill_count": 1},
    },
]

OCCURRENCES = [
    {
        "routine_id": "rtn_001",
        "title": "吃血壓藥",
        "type": "medication",
        "scheduled_at": "2026-07-26T09:00:00.000+08:00",
        "status": "done",
        "completed_at": "2026-07-26T09:05:00.000+08:00",
        "completed_by": "conversation",
    },
    {
        "routine_id": "rtn_002",
        "title": "量血壓",
        "type": "other",
        "scheduled_at": "2026-07-26T19:00:00.000+08:00",
        "status": "missed",
    },
]


@pytest.fixture
def stubs():
    """把資料層全部換成可控的 stub；這一層要測的是組裝與判定，不是 DynamoDB。"""
    with (
        patch.object(summarizer.db, "list_events") as list_events,
        patch.object(summarizer.db, "list_turns_by_day") as list_turns,
        patch.object(summarizer.sessions, "list_pending_sessions") as pending,
        patch.object(summarizer.routines_module, "list_occurrences") as occurrences,
    ):
        list_events.return_value = (DAY_EVENTS, None)
        list_turns.return_value = [
            {"conversation_id": "cnv_1", "session_id": "ses_a", "request_status": "completed"},
            {"conversation_id": "cnv_2", "session_id": "ses_a", "request_status": "completed"},
            {"conversation_id": "cnv_3", "session_id": "ses_b", "request_status": "failed"},
        ]
        pending.return_value = []
        occurrences.return_value = OCCURRENCES
        yield {
            "list_events": list_events,
            "list_turns": list_turns,
            "pending": pending,
            "occurrences": occurrences,
        }


def build(stubs, **kwargs):
    client = kwargs.pop("client", None) or FakeConverseClient(MODEL_OUTPUT)
    summary = summarizer.build_summary(ELDER, DATE, input_through_at=CUTOFF, client=client, **kwargs)
    return summary, client


# -- 事實由程式算 -------------------------------------------------------------


def test_counts_only_completed_turns(stubs):
    summary, _ = build(stubs)
    assert summary["interaction_count"] == 2


def test_routines_block_comes_from_occurrences(stubs):
    summary, _ = build(stubs)
    assert summary["routines"] == {
        "completed": 1,
        "missed": 1,
        "items": [
            {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "done"},
            {"routine_id": "rtn_002", "title": "量血壓", "status": "missed"},
        ],
    }


def test_complete_when_no_pending_session(stubs):
    summary, _ = build(stubs)
    assert summary["data_status"] == "complete"
    assert summary["pending_session_count"] == 0


def test_partial_when_session_still_pending(stubs):
    stubs["pending"].return_value = [{"session_id": "ses_b", "state": "closed"}]
    summary, client = build(stubs)
    assert summary["data_status"] == "partial"
    assert summary["pending_session_count"] == 1
    # partial 時要提醒模型別把總覽寫成一整天的定論
    assert "資料不完整" in client.requests[0]["messages"][0]["content"][0]["text"]


def test_cutoff_drives_occurrence_snapshot(stubs):
    build(stubs)
    assert stubs["occurrences"].call_args.kwargs["cutoff"] == CUTOFF


def test_input_through_at_is_normalized(stubs):
    summary, _ = build(stubs)
    assert summary["input_through_at"] == CUTOFF
    assert summary["generated_at"].endswith("+08:00")
    assert summary["generator_version"] == summarizer.DEFAULT_GENERATOR_VERSION


# -- 模型輸出處理 -------------------------------------------------------------


def test_sections_always_have_seven_keys(stubs):
    client = FakeConverseClient(json.dumps({"overview": "還好", "sections": {"diet": "三餐正常"}, "alerts": []}))
    summary, _ = build(stubs, client=client)
    assert set(summary["sections"]) == set(SUMMARY_SECTION_KEYS)
    assert summary["sections"]["diet"] == "三餐正常"
    assert summary["sections"]["sleep"] is None


def test_unknown_sections_are_dropped(stubs):
    client = FakeConverseClient(
        json.dumps({"overview": "x", "sections": {"diet": "a", "mood": "b"}, "alerts": []})
    )
    summary, _ = build(stubs, client=client)
    assert "mood" not in summary["sections"]


def test_blank_sections_become_null(stubs):
    client = FakeConverseClient(
        json.dumps({"overview": "x", "sections": {"diet": "   ", "sleep": None}, "alerts": ["", " "]})
    )
    summary, _ = build(stubs, client=client)
    assert summary["sections"]["diet"] is None
    assert summary["alerts"] == []


def test_prompt_carries_details_not_transcripts(stubs):
    _, client = build(stubs)
    prompt = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "早餐後服用血壓藥一顆" in prompt
    assert "medication_item=血壓藥" in prompt
    assert "吃血壓藥：done" in prompt
    assert "elder_transcript" not in prompt


def test_events_are_ordered_chronologically_in_prompt(stubs):
    _, client = build(stubs)
    prompt = client.requests[0]["messages"][0]["content"][0]["text"]
    assert prompt.index("09:05") < prompt.index("14:30")


def test_summary_model_override(monkeypatch, stubs):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "main-model")
    monkeypatch.setenv("BEDROCK_SUMMARY_MODEL_ID", "cheap-model")
    _, client = build(stubs)
    assert client.requests[0]["modelId"] == "cheap-model"

    monkeypatch.delenv("BEDROCK_SUMMARY_MODEL_ID")
    _, client = build(stubs)
    assert client.requests[0]["modelId"] == "main-model"


# -- 空資料日 -----------------------------------------------------------------


def test_empty_day_skips_the_model(stubs):
    stubs["list_events"].return_value = ([], None)
    stubs["list_turns"].return_value = []
    stubs["occurrences"].return_value = []

    client = FakeConverseClient(MODEL_OUTPUT)
    summary = summarizer.build_summary(ELDER, DATE, input_through_at=CUTOFF, client=client)

    assert client.requests == []
    assert summary["overview"] == summarizer.EMPTY_OVERVIEW
    assert all(value is None for value in summary["sections"].values())
    assert summary["alerts"] == []
    assert summary["interaction_count"] == 0
    assert summary["data_status"] == "complete"
    assert summary["routines"] == {"completed": 0, "missed": 0, "items": []}


# -- 跨日 alerts 線索 ---------------------------------------------------------


def test_recent_signals_are_limited_to_previous_days(stubs, monkeypatch):
    monkeypatch.setenv("SUMMARY_ALERT_LOOKBACK_DAYS", "3")
    build(stubs)
    lookback_calls = [
        call.kwargs
        for call in stubs["list_events"].call_args_list
        if call.kwargs.get("event_type") in summarizer.ALERT_EVENT_TYPES
    ]
    assert lookback_calls
    for kwargs in lookback_calls:
        assert kwargs["from_date"] == "2026-07-24"
        assert kwargs["to_date"] == "2026-07-25"


def test_lookback_can_be_disabled(stubs, monkeypatch):
    monkeypatch.setenv("SUMMARY_ALERT_LOOKBACK_DAYS", "1")
    build(stubs)
    assert not [
        call
        for call in stubs["list_events"].call_args_list
        if call.kwargs.get("event_type") in summarizer.ALERT_EVENT_TYPES
    ]


def test_day_events_are_truncated_at_limit(stubs, monkeypatch):
    monkeypatch.setenv("SUMMARY_MAX_EVENTS", "1")
    stubs["list_events"].return_value = (DAY_EVENTS, None)
    events = summarizer.collect_day_events(ELDER, DATE)
    # 截尾保留較早的事件，摘要才能按時間敘事
    assert [item["event_id"] for item in events] == ["evt_1"]


# -- 寫入 ---------------------------------------------------------------------


def test_generate_and_store_persists_and_strips_internal_metadata(stubs):
    client = FakeConverseClient(MODEL_OUTPUT)
    with patch.object(summarizer.db, "put_daily_summary", side_effect=lambda item: (item, True)) as put:
        stored, written = summarizer.generate_and_store(
            ELDER, DATE, input_through_at=CUTOFF, client=client
        )
    assert written is True
    assert "_model_metadata" not in put.call_args.args[0]
    assert stored["date"] == DATE


def test_generate_and_store_reports_rejected_write(stubs):
    existing = {"date": DATE, "data_status": "complete", "overview": "既有版本"}
    client = FakeConverseClient(MODEL_OUTPUT)
    with patch.object(summarizer.db, "put_daily_summary", return_value=(existing, False)):
        stored, written = summarizer.generate_and_store(
            ELDER, DATE, input_through_at=CUTOFF, client=client
        )
    assert written is False
    assert stored is existing
