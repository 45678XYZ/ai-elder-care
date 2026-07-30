"""routine occurrence 衍生測試（moto）。

對應 docs/api.md 的三條規則：occurrence_cutoff 封頂、completion-first 取完成當時版本、
未完成才以 cutoff 前最新有效版本展開且每日最多一筆。
"""

import importlib

import boto3
import pytest
from moto import mock_aws

ROUTINES_TABLE = "routines-test"
EVENTS_TABLE = "events-test"

ELDER = "eld_a1b2c3d4e5f6"
DATE = "2026-07-26"  # 週日
CUTOFF_EVENING = "2026-07-26T20:00:00.000+08:00"


@pytest.fixture
def routines(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_ROUTINES", ROUTINES_TABLE)
    monkeypatch.setenv("TABLE_EVENTS", EVENTS_TABLE)

    with mock_aws():
        resource = boto3.resource("dynamodb")
        resource.create_table(
            TableName=ROUTINES_TABLE,
            KeySchema=[
                {"AttributeName": "routine_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "routine_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "version_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "routine-versions-by-elder",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "version_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=EVENTS_TABLE,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "event_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "event_id", "AttributeType": "S"},
                {"AttributeName": "event_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "events-by-time",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "event_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        importlib.reload(importlib.import_module("src.shared.db"))
        module = importlib.reload(importlib.import_module("src.shared.routines"))
        yield module

    importlib.reload(importlib.import_module("src.shared.db"))
    importlib.reload(importlib.import_module("src.shared.routines"))


def put_version(
    routines,
    routine_id: str,
    version: int,
    *,
    effective_from: str,
    title: str,
    schedule: dict,
    active: bool = True,
    routine_type: str = "medication",
):
    routines.db.get_dynamodb_resource().Table(routines.db.TABLE_ROUTINES).put_item(
        Item={
            "routine_id": routine_id,
            "version": version,
            "elder_id": ELDER,
            "version_time_key": f"{effective_from}#{routine_id}#{version}",
            "effective_from": effective_from,
            "is_current": True,
            "active": active,
            "remind": True,
            "title": title,
            "type": routine_type,
            "schedule": schedule,
        }
    )


def put_completion(routines, routine_id: str, *, ts: str, version: int | None = 1, by="conversation"):
    return routines.db.complete_routine_with_event(
        elder_id=ELDER,
        routine_id=routine_id,
        routine_date=DATE,
        ts=ts,
        completed_by=by,
        routine_version=version,
        event_type="medication",
    )


DAILY_9AM = {"freq": "daily", "time": "09:00"}


# -- cutoff -------------------------------------------------------------------


def test_cutoff_is_capped_at_day_boundary(routines):
    limit = routines.occurrence_cutoff(DATE, "2026-07-28T10:00:00+08:00")
    assert routines.format_ts(limit) == "2026-07-26T23:59:59.999+08:00"


def test_cutoff_uses_given_moment_when_within_day(routines):
    limit = routines.occurrence_cutoff(DATE, CUTOFF_EVENING)
    assert routines.format_ts(limit) == CUTOFF_EVENING


# -- 未完成 occurrence --------------------------------------------------------


def test_pending_before_grace_expires(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="吃血壓藥", schedule=DAILY_9AM)
    items = routines.list_occurrences(ELDER, DATE, cutoff="2026-07-26T09:30:00+08:00", grace=120)
    assert [(item["routine_id"], item["status"]) for item in items] == [("rtn_001", "pending")]
    assert items[0]["scheduled_at"] == "2026-07-26T09:00:00+08:00"

    assert "completed_at" not in items[0]


def test_missed_after_grace_expires(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="吃血壓藥", schedule=DAILY_9AM)
    items = routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING, grace=120)
    assert items[0]["status"] == "missed"


def test_versions_effective_after_cutoff_are_ignored(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-27T08:00:00.000+08:00", title="明天才生效", schedule=DAILY_9AM)
    assert routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING) == []


def test_deactivated_latest_version_produces_no_occurrence(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="量血壓", schedule=DAILY_9AM)
    put_version(
        routines,
        "rtn_001",
        2,
        effective_from="2026-07-20T10:00:00.000+08:00",
        title="量血壓",
        schedule=DAILY_9AM,
        active=False,
    )
    assert routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING) == []


def test_same_day_revision_supersedes_without_second_occurrence(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="吃血壓藥", schedule=DAILY_9AM)
    put_version(
        routines,
        "rtn_001",
        2,
        effective_from="2026-07-26T11:00:00.000+08:00",
        title="吃血壓藥（改晚上）",
        schedule={"freq": "daily", "time": "19:00"},
    )
    items = routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING, grace=120)
    assert len(items) == 1
    assert items[0]["title"] == "吃血壓藥（改晚上）"
    assert items[0]["scheduled_at"] == "2026-07-26T19:00:00+08:00"

    # 19:00 + 120 分 > 20:00 cutoff
    assert items[0]["status"] == "pending"


@pytest.mark.parametrize(
    "schedule,expected",
    [
        ({"freq": "weekly", "weekday": 7, "time": "09:00"}, 1),  # 2026-07-26 是週日
        ({"freq": "weekly", "weekday": 3, "time": "09:00"}, 0),
        ({"freq": "once", "date": DATE, "time": "15:00"}, 1),
        ({"freq": "once", "date": "2026-07-27", "time": "15:00"}, 0),
    ],
)
def test_schedule_kinds(routines, schedule, expected):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="行程", schedule=schedule)
    assert len(routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING)) == expected


# -- completion-first ---------------------------------------------------------


def test_completion_wins_and_uses_recorded_version(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="吃血壓藥", schedule=DAILY_9AM)
    put_completion(routines, "rtn_001", ts="2026-07-26T09:05:00+08:00", version=1)
    # 完成後同日改版
    put_version(
        routines,
        "rtn_001",
        2,
        effective_from="2026-07-26T12:00:00.000+08:00",
        title="吃血壓藥（改名）",
        schedule={"freq": "daily", "time": "20:00"},
    )

    items = routines.list_occurrences(ELDER, DATE, cutoff="2026-07-26T23:00:00+08:00")
    assert len(items) == 1
    assert items[0]["status"] == "done"
    # 顯示定義取完成當下的版本，不受後續改版影響
    assert items[0]["title"] == "吃血壓藥"
    assert items[0]["scheduled_at"] == "2026-07-26T09:00:00+08:00"

    assert items[0]["completed_at"] == "2026-07-26T09:05:00.000+08:00"
    assert items[0]["completed_by"] == "conversation"


def test_completion_prevents_missed_even_after_grace(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="吃血壓藥", schedule=DAILY_9AM)
    put_completion(routines, "rtn_001", ts="2026-07-26T09:05:00+08:00")
    items = routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING, grace=120)
    assert items[0]["status"] == "done"


def test_completion_falls_back_when_recorded_version_missing(routines):
    put_version(routines, "rtn_001", 1, effective_from="2026-07-01T10:00:00.000+08:00", title="現存版本", schedule=DAILY_9AM)
    put_completion(routines, "rtn_001", ts="2026-07-26T09:05:00+08:00", version=99)
    items = routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING)
    assert items[0]["status"] == "done"
    assert items[0]["title"] == "現存版本"


def test_non_completion_events_do_not_create_occurrences(routines):
    routines.db.create_event(
        {
            "elder_id": ELDER,
            "canonical_event_key": f"{DATE}#SLOT_0900#長者#服用血壓藥",
            "ts": "2026-07-26T09:05:00+08:00",
            "type": "medication",
            "detail": "疑似完成，但只是一般事件",
            "structured_detail": {"suspected_routine_id": "rtn_001"},
        }
    )
    assert routines.list_occurrences(ELDER, DATE, cutoff=CUTOFF_EVENING) == []


# -- 摘要快照 -----------------------------------------------------------------


def test_summary_snapshot_counts_only_done_and_missed(routines):
    occurrences = [
        {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "done", "scheduled_at": "x"},
        {"routine_id": "rtn_002", "title": "量血壓", "status": "missed", "scheduled_at": "y"},
        {"routine_id": "rtn_003", "title": "散步", "status": "pending", "scheduled_at": "z"},
    ]
    snapshot = routines.summary_snapshot(occurrences)
    assert snapshot["completed"] == 1
    assert snapshot["missed"] == 1
    assert snapshot["items"] == [
        {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "done"},
        {"routine_id": "rtn_002", "title": "量血壓", "status": "missed"},
        {"routine_id": "rtn_003", "title": "散步", "status": "pending"},
    ]


def test_grace_minutes_reads_environment(routines, monkeypatch):
    monkeypatch.setenv("ROUTINE_GRACE_MINUTES", "30")
    assert routines.grace_minutes() == 30
    monkeypatch.setenv("ROUTINE_GRACE_MINUTES", "not-a-number")
    assert routines.grace_minutes() == routines.DEFAULT_GRACE_MINUTES
