"""時間正規化與相對時間解析測試。

冪等性建立在「同一輸入必得同一輸出」上，因此這裡特別驗證參考時間必填、
台灣日界、固定毫秒精度與排序鍵可比大小。
"""

from datetime import datetime, timezone, timedelta

import pytest

from src.extraction.temporal import (
    TZ_TAIPEI,
    TemporalError,
    day_end,
    day_key,
    day_start,
    format_ts,
    normalize_ts,
    parse_ts,
    resolve_expression,
    resolve_observed_at,
)

REF = "2026-07-26T09:41:23.456+08:00"


def test_format_uses_fixed_millisecond_precision():
    """精度不一致會讓 event_time_key 的字串排序失準。"""
    assert format_ts(datetime(2026, 7, 26, 9, 5, tzinfo=TZ_TAIPEI)) == "2026-07-26T09:05:00.000+08:00"
    assert normalize_ts("2026-07-26T09:05:00+08:00") == "2026-07-26T09:05:00.000+08:00"
    assert normalize_ts("2026-07-26T09:05:00.1+08:00") == "2026-07-26T09:05:00.100+08:00"
    assert normalize_ts("2026-07-26T09:05:00.123456+08:00") == "2026-07-26T09:05:00.123+08:00"


def test_normalize_converts_other_timezones_to_taipei():
    assert normalize_ts("2026-07-26T01:05:00+00:00") == "2026-07-26T09:05:00.000+08:00"
    assert normalize_ts("2026-07-26T01:05:00Z") == "2026-07-26T09:05:00.000+08:00"


def test_naive_input_is_treated_as_taipei():
    assert normalize_ts("2026-07-26T09:05:00") == "2026-07-26T09:05:00.000+08:00"


def test_normalized_strings_sort_chronologically():
    early = normalize_ts("2026-07-26T09:05:00+08:00")
    late = normalize_ts("2026-07-26T09:05:00.001+08:00")
    next_day = normalize_ts("2026-07-27T00:00:00+08:00")
    assert early < late < next_day


def test_invalid_input_raises():
    with pytest.raises(TemporalError):
        parse_ts("昨天晚上")
    with pytest.raises(TemporalError):
        parse_ts("")


def test_day_boundaries():
    assert day_key("2026-07-26T23:59:59.999+08:00") == "2026-07-26"
    # UTC 上仍是 26 日 16:00，但台灣日界已經是 27 日
    assert day_key("2026-07-26T16:30:00+00:00") == "2026-07-27"
    assert day_start("2026-07-26") == "2026-07-26T00:00:00.000+08:00"
    assert day_end("2026-07-26") == "2026-07-26T23:59:59.999+08:00"


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("昨天晚上", "2026-07-25T20:00:00.000+08:00"),
        ("昨晚", "2026-07-25T20:00:00.000+08:00"),
        ("今天早上", "2026-07-26T08:00:00.000+08:00"),
        ("前天中午", "2026-07-24T12:00:00.000+08:00"),
        ("大前天下午", "2026-07-23T14:00:00.000+08:00"),
        ("三天前", "2026-07-23T09:41:23.456+08:00"),
        ("凌晨", "2026-07-26T03:00:00.000+08:00"),
        ("傍晚", "2026-07-26T18:00:00.000+08:00"),
        ("上週", "2026-07-19T09:41:23.456+08:00"),
    ],
)
def test_relative_expressions(expression, expected):
    assert format_ts(resolve_expression(expression, REF)) == expected


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("早上八點", "2026-07-26T08:00:00.000+08:00"),
        ("下午三點", "2026-07-26T15:00:00.000+08:00"),
        ("晚上八點半", "2026-07-26T20:30:00.000+08:00"),
        ("中午十二點", "2026-07-26T12:00:00.000+08:00"),
        ("昨天晚上九點", "2026-07-25T21:00:00.000+08:00"),
        ("7:05", "2026-07-26T07:05:00.000+08:00"),
        ("昨天 21:30", "2026-07-25T21:30:00.000+08:00"),
    ],
)
def test_explicit_clock_times(expression, expected):
    assert format_ts(resolve_expression(expression, REF)) == expected


def test_absolute_date_expression():
    assert format_ts(resolve_expression("2026-07-20 早上", REF)) == "2026-07-20T08:00:00.000+08:00"
    assert format_ts(resolve_expression("2026年7月20日", REF)) == "2026-07-20T09:41:23.456+08:00"


def test_expression_crossing_taipei_day_boundary():
    """參考時間在台灣的凌晨時，「昨天晚上」必須落在前一個台灣日。"""
    reference = "2026-07-26T00:30:00.000+08:00"
    assert format_ts(resolve_expression("昨天晚上", reference)) == "2026-07-25T20:00:00.000+08:00"
    # 以 UTC 表示的同一時刻要得到同樣結果
    utc_reference = datetime(2026, 7, 25, 16, 30, tzinfo=timezone.utc)
    assert format_ts(resolve_expression("昨天晚上", utc_reference)) == "2026-07-25T20:00:00.000+08:00"


def test_empty_or_unknown_expression_falls_back_to_reference():
    """不猜測：認不出來就用來源 turn 的時間，而不是捏造 00:00。"""
    assert format_ts(resolve_expression(None, REF)) == "2026-07-26T09:41:23.456+08:00"
    assert format_ts(resolve_expression("   ", REF)) == "2026-07-26T09:41:23.456+08:00"
    assert format_ts(resolve_expression("那個時候", REF)) == "2026-07-26T09:41:23.456+08:00"


def test_resolution_is_deterministic_for_retry():
    """同一 snapshot 重跑必須得到同一結果，否則 canonical key 會漂移。"""
    first = resolve_expression("昨天晚上吃藥的時候", REF)
    second = resolve_expression("昨天晚上吃藥的時候", REF)
    assert first == second
    # 參考時間不同才允許結果不同
    other = resolve_expression("昨天晚上吃藥的時候", "2026-07-27T09:41:23.456+08:00")
    assert other != first


def test_resolve_observed_at_prefers_model_absolute_value():
    assert (
        resolve_observed_at("2026-07-25T20:15:00+08:00", "昨天晚上", REF)
        == "2026-07-25T20:15:00.000+08:00"
    )


def test_resolve_observed_at_falls_back_to_expression():
    assert resolve_observed_at(None, "昨天晚上", REF) == "2026-07-25T20:00:00.000+08:00"
    assert resolve_observed_at("昨天晚上", None, REF) == "2026-07-25T20:00:00.000+08:00"
    assert resolve_observed_at(None, None, REF) == "2026-07-26T09:41:23.456+08:00"


def test_reference_is_required_no_wall_clock():
    """解析函式簽章不提供預設值，杜絕不小心用到 datetime.now()。"""
    with pytest.raises(TypeError):
        resolve_expression("昨天")  # type: ignore[call-arg]
