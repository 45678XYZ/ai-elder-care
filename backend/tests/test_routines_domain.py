"""src.shared.routines 單元測試：版本收斂、occurrence 狀態推導與冪等鍵。"""
from datetime import datetime

from src.shared import routines


ELDER_ID = "eld_001"


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _version(**overrides):
    item = {
        "routine_id": "rtn_001",
        "elder_id": ELDER_ID,
        "version": 1,
        "effective_from": "2026-07-01T10:00:00.000+08:00",
        "active": True,
        "title": "吃血壓藥",
        "type": "medication",
        "schedule": {"freq": "daily", "time": "09:00"},
    }
    item.update(overrides)
    return item


# --- 時間與排程 ---


def test_occurrence_cutoff_caps_at_taiwan_day_end():
    # 查當日：cutoff 為查詢當下
    assert routines.occurrence_cutoff("2026-07-14", _at("2026-07-14T12:00:00+08:00")) == _at(
        "2026-07-14T12:00:00+08:00"
    )
    # 查歷史日期：cutoff 封頂於該日台灣日界結束，之後的改版不得回頭改寫
    assert routines.occurrence_cutoff("2026-07-14", _at("2026-07-20T12:00:00+08:00")) == _at(
        "2026-07-14T23:59:59.999+08:00"
    )


def test_scheduled_at_by_freq():
    daily = {"freq": "daily", "time": "09:00"}
    assert routines.scheduled_at("2026-07-14", daily) == "2026-07-14T09:00:00+08:00"

    # 2026-07-14 為週二（isoweekday=2）
    weekly = {"freq": "weekly", "weekday": 2, "time": "19:00"}
    assert routines.scheduled_at("2026-07-14", weekly) == "2026-07-14T19:00:00+08:00"
    assert routines.scheduled_at("2026-07-15", weekly) is None

    once = {"freq": "once", "date": "2026-07-15", "time": "15:00"}
    assert routines.scheduled_at("2026-07-15", once) == "2026-07-15T15:00:00+08:00"
    assert routines.scheduled_at("2026-07-14", once) is None


def test_select_effective_version_picks_latest_before_cutoff():
    v1 = _version(version=1, effective_from="2026-07-01T10:00:00.000+08:00")
    v2 = _version(version=2, effective_from="2026-07-14T10:00:00.000+08:00")

    assert routines.select_effective_version([v1, v2], _at("2026-07-14T09:00:00+08:00")) is v1
    assert routines.select_effective_version([v1, v2], _at("2026-07-14T12:00:00+08:00")) is v2
    assert routines.select_effective_version([v1, v2], _at("2026-06-30T12:00:00+08:00")) is None


# --- occurrence 狀態 ---


def test_pending_before_grace_and_missed_after():
    versions = [_version()]

    pending = routines.resolve_occurrence(
        versions, None, "2026-07-14", _at("2026-07-14T10:00:00+08:00"), grace_override=120
    )
    assert pending["status"] == "pending"
    assert pending["scheduled_at"] == "2026-07-14T09:00:00+08:00"
    assert "completed_at" not in pending

    missed = routines.resolve_occurrence(
        versions, None, "2026-07-14", _at("2026-07-14T12:00:00+08:00"), grace_override=120
    )
    assert missed["status"] == "missed"


def test_inactive_or_unscheduled_version_has_no_occurrence():
    inactive = [_version(active=False)]
    assert (
        routines.resolve_occurrence(inactive, None, "2026-07-14", _at("2026-07-14T10:00:00+08:00"))
        is None
    )

    other_day = [_version(schedule={"freq": "once", "date": "2026-07-15", "time": "15:00"})]
    assert (
        routines.resolve_occurrence(
            other_day, None, "2026-07-14", _at("2026-07-14T10:00:00+08:00")
        )
        is None
    )


def test_completion_first_keeps_version_used_at_completion():
    """完成後同日改版，occurrence 仍保留完成當時的定義與完成資料。"""
    v1 = _version(version=1)
    v2 = _version(
        version=2,
        effective_from="2026-07-14T14:00:00.000+08:00",
        title="改成晚上吃血壓藥",
        schedule={"freq": "daily", "time": "20:00"},
    )
    completion = {
        "routine_id": "rtn_001",
        "routine_version": 1,
        "routine_date": "2026-07-14",
        "ts": "2026-07-14T09:05:00.000+08:00",
        "completed_by": "conversation",
    }

    occurrence = routines.resolve_occurrence(
        [v1, v2], completion, "2026-07-14", _at("2026-07-14T20:30:00+08:00")
    )
    assert occurrence["status"] == "done"
    assert occurrence["title"] == "吃血壓藥"
    assert occurrence["scheduled_at"] == "2026-07-14T09:00:00+08:00"
    assert occurrence["completed_at"] == "2026-07-14T09:05:00.000+08:00"
    assert occurrence["completed_by"] == "conversation"


def test_later_version_does_not_rewrite_past_date():
    """跨日之後才生效的版本，不得改寫已封頂的歷史日期。"""
    v1 = _version(version=1, schedule={"freq": "daily", "time": "09:00"})
    v2 = _version(
        version=2, effective_from="2026-07-15T08:00:00.000+08:00", active=False
    )

    occurrence = routines.resolve_occurrence(
        [v1, v2], None, "2026-07-14", _at("2026-07-16T10:00:00+08:00")
    )
    assert occurrence["status"] == "missed"  # 仍以 07-14 當時有效的 v1 判定


def test_resolve_occurrences_is_sorted_and_one_per_routine():
    versions = [
        _version(routine_id="rtn_001", version=1, schedule={"freq": "daily", "time": "19:00"}),
        _version(
            routine_id="rtn_001",
            version=2,
            effective_from="2026-07-14T08:00:00.000+08:00",
            schedule={"freq": "daily", "time": "18:00"},
        ),
        _version(routine_id="rtn_002", title="量血壓", schedule={"freq": "daily", "time": "09:00"}),
    ]

    items = routines.resolve_occurrences(
        versions, {}, "2026-07-14", _at("2026-07-14T10:00:00+08:00"), grace_override=120
    )
    assert [item["routine_id"] for item in items] == ["rtn_002", "rtn_001"]
    # 同日改版只收斂成一筆，取 cutoff 前最新版本的排程
    assert items[1]["scheduled_at"] == "2026-07-14T18:00:00+08:00"


# --- 冪等鍵 ---


def test_stable_id_is_deterministic_and_scoped():
    same = routines.stable_id("rtn_", ELDER_ID, "usr_care", "req-1")
    assert same == routines.stable_id("rtn_", ELDER_ID, "usr_care", "req-1")
    assert same.startswith("rtn_")
    assert same != routines.stable_id("rtn_", ELDER_ID, "usr_care", "req-2")
    assert same != routines.stable_id("rtn_", ELDER_ID, "usr_other", "req-1")
    # 分隔符確保不同組合不會拼成同一字串
    assert routines.stable_id("rtn_", "a", "bc") != routines.stable_id("rtn_", "ab", "c")


def test_request_hash_ignores_key_order_only():
    payload = {"title": "吃血壓藥", "remind": True}
    assert routines.request_hash(payload) == routines.request_hash(
        {"remind": True, "title": "吃血壓藥"}
    )
    assert routines.request_hash(payload) != routines.request_hash(
        {"title": "吃血壓藥", "remind": False}
    )
