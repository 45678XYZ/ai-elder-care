"""排程摘要 generator 測試。

鎖住 docs/feature_daily-summarization.md §7：nightly 不等 batch（有 pending 就寫 partial），
backfill 只重算等待窗口內的 partial，超過窗口就放手，單一長者失敗不影響其他人。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.extraction.temporal import TZ_TAIPEI, format_ts
from src.handlers import summary_generator as generator
from src.shared import bedrock

ELDER_A = "eld_aaaaaaaaaaaa"
ELDER_B = "eld_bbbbbbbbbbbb"


def summary(elder_id: str, date: str, *, status: str, minutes_ago: int = 10):
    generated_at = format_ts(datetime.now(TZ_TAIPEI) - timedelta(minutes=minutes_ago))
    return {
        "elder_id": elder_id,
        "date": date,
        "data_status": status,
        "generated_at": generated_at,
        "pending_session_count": 1 if status == "partial" else 0,
    }


@pytest.fixture
def elders():
    with patch.object(
        generator.db,
        "list_elders",
        return_value=[{"elder_id": ELDER_A}, {"elder_id": ELDER_B}],
    ) as mock:
        yield mock


# -- nightly ------------------------------------------------------------------


def test_nightly_generates_for_every_elder(elders):
    with patch.object(
        generator.summarizer,
        "generate_and_store",
        side_effect=lambda elder_id, date: (summary(elder_id, date, status="complete"), True),
    ) as mock_generate:
        result = generator.handler({}, None)

    assert result["mode"] == "nightly"
    assert result["generated"] == 2
    assert result["partial"] == 0
    assert [call.args[0] for call in mock_generate.call_args_list] == [ELDER_A, ELDER_B]


def test_nightly_writes_partial_without_waiting(elders):
    with patch.object(
        generator.summarizer,
        "generate_and_store",
        side_effect=lambda elder_id, date: (summary(elder_id, date, status="partial"), True),
    ):
        result = generator.handler({"mode": "nightly", "date": "2026-07-26"}, None)

    assert result["partial"] == 2
    assert result["date"] == "2026-07-26"


def test_nightly_isolates_single_elder_failure(elders):
    def side_effect(elder_id, date):
        if elder_id == ELDER_A:
            raise bedrock.BedrockError("boom")
        return summary(elder_id, date, status="complete"), True

    with patch.object(generator.summarizer, "generate_and_store", side_effect=side_effect):
        result = generator.handler({}, None)

    assert result["failed"] == 1
    assert result["generated"] == 1


def test_explicit_elder_ids_skip_the_scan(elders):
    with patch.object(
        generator.summarizer,
        "generate_and_store",
        side_effect=lambda elder_id, date: (summary(elder_id, date, status="complete"), True),
    ) as mock_generate:
        result = generator.handler({"elder_ids": [ELDER_B]}, None)

    assert elders.call_count == 0
    assert result["generated"] == 1
    assert mock_generate.call_args.args[0] == ELDER_B


def test_sweep_limit_caps_the_batch(elders, monkeypatch):
    monkeypatch.setenv("SUMMARY_SWEEP_LIMIT", "1")
    with patch.object(
        generator.summarizer,
        "generate_and_store",
        side_effect=lambda elder_id, date: (summary(elder_id, date, status="complete"), True),
    ):
        result = generator.handler({}, None)
    assert result["elders"] == 1


# -- backfill -----------------------------------------------------------------


def test_backfill_regenerates_partial_within_window(elders, monkeypatch):
    monkeypatch.setenv("SUMMARY_WAIT_MINUTES", "180")
    today = generator.day_key(datetime.now(TZ_TAIPEI))
    with (
        patch.object(
            generator.db,
            "list_daily_summaries",
            return_value=([summary(ELDER_A, today, status="partial", minutes_ago=30)], None),
        ),
        patch.object(
            generator.summarizer,
            "generate_and_store",
            side_effect=lambda elder_id, date: (summary(elder_id, date, status="complete"), True),
        ) as mock_generate,
    ):
        result = generator.handler({"mode": "backfill"}, None)

    assert result["regenerated"] == 2  # 兩位長者各一天
    assert result["upgraded"] == 2
    assert mock_generate.call_args.args[1] == today


def test_backfill_skips_complete_summaries(elders):
    today = generator.day_key(datetime.now(TZ_TAIPEI))
    with (
        patch.object(
            generator.db,
            "list_daily_summaries",
            return_value=([summary(ELDER_A, today, status="complete")], None),
        ),
        patch.object(generator.summarizer, "generate_and_store") as mock_generate,
    ):
        result = generator.handler({"mode": "backfill"}, None)

    assert result["regenerated"] == 0
    assert mock_generate.call_count == 0


def test_backfill_gives_up_outside_window(elders, monkeypatch):
    monkeypatch.setenv("SUMMARY_WAIT_MINUTES", "60")
    today = generator.day_key(datetime.now(TZ_TAIPEI))
    with (
        patch.object(
            generator.db,
            "list_daily_summaries",
            return_value=([summary(ELDER_A, today, status="partial", minutes_ago=600)], None),
        ),
        patch.object(generator.summarizer, "generate_and_store") as mock_generate,
    ):
        result = generator.handler({"mode": "backfill"}, None)

    assert result["skipped_stale"] == 2
    assert mock_generate.call_count == 0


def test_backfill_treats_missing_generated_at_as_stale(elders):
    today = generator.day_key(datetime.now(TZ_TAIPEI))
    broken = {"elder_id": ELDER_A, "date": today, "data_status": "partial"}
    with (
        patch.object(generator.db, "list_daily_summaries", return_value=([broken], None)),
        patch.object(generator.summarizer, "generate_and_store") as mock_generate,
    ):
        result = generator.handler({"mode": "backfill"}, None)

    assert result["skipped_stale"] == 2
    assert mock_generate.call_count == 0


def test_backfill_survives_read_failure(elders):
    with patch.object(
        generator.db, "list_daily_summaries", side_effect=generator.db.DBError("boom")
    ):
        result = generator.handler({"mode": "backfill"}, None)
    assert result["failed"] == 2
    assert result["regenerated"] == 0


def test_backfill_range_covers_configured_days(elders, monkeypatch):
    monkeypatch.setenv("SUMMARY_BACKFILL_DAYS", "3")
    with (
        patch.object(generator.db, "list_daily_summaries", return_value=([], None)) as mock_list,
        patch.object(generator.summarizer, "generate_and_store"),
    ):
        result = generator.handler({"mode": "backfill"}, None)

    kwargs = mock_list.call_args.kwargs
    assert kwargs["from_date"] == generator.day_key(datetime.now(TZ_TAIPEI) - timedelta(days=2))
    assert kwargs["to_date"] == result["to"]


def test_elders_scan_failure_yields_empty_sweep():
    with patch.object(generator.db, "list_elders", side_effect=generator.db.DBError("boom")):
        result = generator.handler({}, None)
    assert result["elders"] == 0
    assert result["generated"] == 0
