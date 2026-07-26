"""時間正規化與相對時間解析。

改寫自 aws-hackathon 的 `temporal_resolver`，三個關鍵差異：

1. **一律帶台灣時區與固定毫秒精度**。`ts` 會被串進 `event_time_key`，字串排序即時間排序，
   精度不一致會讓排序鍵錯亂（規範見 docs/framework.md 的 events 表）。
2. **參考時間必填，不得使用 `datetime.now()`**。batch 可能被 retry、duplicate delivery 或
   DLQ replay 重跑，只要參考時間會漂移，同一段對話就會算出不同的 Slot 與 canonical key，
   冪等性直接破功。參考時間一律取自 turn 的 `created_at`。
3. **日界以 +08:00 計算**。跨日的相對時間（「昨天晚上」）必須落在台灣日界內。
"""

from datetime import date, datetime, timedelta, timezone
import re
import unicodedata

TZ_TAIPEI = timezone(timedelta(hours=8), name="+08:00")

# 相對時間的時段對照；沒有明確鐘點時代表該時段的代表時刻
_PERIOD_HOURS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("凌晨", "深夜", "半夜"), 3),
    (("清晨", "早晨", "早上", "上午", "今早"), 8),
    (("中午", "正午"), 12),
    (("下午", "午後", "過午"), 14),
    (("傍晚",), 18),
    # 「昨晚」「今晚」本身就帶時段語意，不能只靠「晚上」比對
    (("晚上", "夜間", "夜裡", "晚間", "昨晚", "今晚", "昨夜"), 20),
)

# 相對日期；順序有意義，較長的詞要先比對（大前天 先於 前天）
_DAY_OFFSETS: tuple[tuple[str, int], ...] = (
    ("大前天", -3),
    ("前天", -2),
    ("昨天", -1),
    ("昨日", -1),
    ("昨晚", -1),
    ("昨夜", -1),
    ("今天", 0),
    ("今日", 0),
    ("今早", 0),
    ("現在", 0),
    ("剛剛", 0),
    ("剛才", 0),
    ("當下", 0),
)

_ZH_NUMERALS: dict[str, int] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}

_DATE_PATTERN = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})")
_DAYS_AGO_PATTERN = re.compile(r"([0-9]+|十[一二]?|[一二兩三四五六七八九])\s*天前")
_WEEKS_AGO_PATTERN = re.compile(r"([0-9]+|十[一二]?|[一二兩三四五六七八九])\s*(?:週|周|星期)前")
_CLOCK_PATTERN = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")
_ZH_HOUR_PATTERN = re.compile(r"([0-9]{1,2}|十[一二]?|[一二兩三四五六七八九])\s*[點点時时]\s*(半|[0-9]{1,2}|[一二兩三四五六七八九十]+)?\s*分?")


class TemporalError(ValueError):
    """時間字串無法解析。"""


def _to_int_zh(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _ZH_NUMERALS.get(token)


def to_taipei(value: datetime) -> datetime:
    """轉成台灣時區；naive 視為已經是台灣時間。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ_TAIPEI)
    return value.astimezone(TZ_TAIPEI)


def parse_ts(value: str | datetime) -> datetime:
    """解析 ISO 8601 字串或 datetime，回傳帶台灣時區的 datetime。"""
    if isinstance(value, datetime):
        return to_taipei(value)
    text = str(value).strip()
    if not text:
        raise TemporalError("時間字串為空")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return to_taipei(datetime.fromisoformat(text))
    except ValueError as exc:
        raise TemporalError(f"無法解析時間字串：{value}") from exc


def format_ts(value: datetime) -> str:
    """輸出固定毫秒精度、帶 +08:00 的 ISO 8601 字串。"""
    taipei = to_taipei(value)
    milliseconds = taipei.microsecond // 1000
    return f"{taipei:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}+08:00"


def normalize_ts(value: str | datetime) -> str:
    """把任意合法時間表示正規化成 `event_time_key` 可用的形式。"""
    return format_ts(parse_ts(value))


def day_key(value: str | datetime) -> str:
    """台灣日界下的 `YYYY-MM-DD`。"""
    return f"{parse_ts(value):%Y-%m-%d}"


def day_start(day: str) -> str:
    """該日台灣日界起點。"""
    return f"{day}T00:00:00.000+08:00"


def day_end(day: str) -> str:
    """該日台灣日界終點；查詢區間上界與 occurrence cutoff 都用這個值。"""
    return f"{day}T23:59:59.999+08:00"


def _match_day_offset(expr: str) -> int | None:
    for keyword, offset in _DAY_OFFSETS:
        if keyword in expr:
            return offset
    match = _DAYS_AGO_PATTERN.search(expr)
    if match:
        days = _to_int_zh(match.group(1))
        if days is not None:
            return -days
    match = _WEEKS_AGO_PATTERN.search(expr)
    if match:
        weeks = _to_int_zh(match.group(1))
        if weeks is not None:
            return -7 * weeks
    if "上週" in expr or "上星期" in expr:
        return -7
    return None


def _match_period_hour(expr: str) -> int | None:
    for keywords, hour in _PERIOD_HOURS:
        if any(keyword in expr for keyword in keywords):
            return hour
    return None


def _match_explicit_time(expr: str) -> tuple[int, int] | None:
    match = _CLOCK_PATTERN.search(expr)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    match = _ZH_HOUR_PATTERN.search(expr)
    if match:
        hour = _to_int_zh(match.group(1))
        if hour is None or not 0 <= hour <= 23:
            return None
        raw_minute = match.group(2)
        if raw_minute == "半":
            minute = 30
        elif raw_minute:
            minute = _to_int_zh(raw_minute)
            if minute is None or not 0 <= minute <= 59:
                minute = 0
        else:
            minute = 0
        return hour, minute
    return None


def resolve_expression(raw_expr: str | None, reference: str | datetime) -> datetime:
    """把相對時間表達解析成絕對時間。

    `reference` 必填，一律傳入來源 turn 的 `created_at`；同樣的輸入必然得到同樣的輸出，
    這是 batch retry 冪等的前提。無法辨識時回傳參考時間本身，不猜測。
    """
    ref = parse_ts(reference)
    if raw_expr is None or not str(raw_expr).strip():
        return ref

    expr = unicodedata.normalize("NFKC", str(raw_expr)).strip()

    # 已經是絕對時間就直接採用
    try:
        return parse_ts(expr)
    except TemporalError:
        pass

    target_day = ref.date()
    date_match = _DATE_PATTERN.search(expr)
    if date_match:
        year, month, day = (int(g) for g in date_match.groups())
        try:
            target_day = date(year, month, day)
        except ValueError as exc:
            raise TemporalError(f"時間表達含非法日期：{raw_expr}") from exc
    else:
        offset = _match_day_offset(expr)
        if offset is not None:
            target_day = ref.date() + timedelta(days=offset)

    period_hour = _match_period_hour(expr)
    explicit = _match_explicit_time(expr)

    if explicit is not None:
        hour, minute = explicit
        # 「下午三點」要換算成 15 點；「中午十二點」維持 12 點
        if period_hour is not None and period_hour >= 12 and hour < 12:
            hour += 12
        second, microsecond = 0, 0
    elif period_hour is not None:
        hour, minute, second, microsecond = period_hour, 0, 0, 0
    else:
        # 只提到日期而沒提到時間時沿用參考時刻，避免無依據地捏造成 00:00
        hour, minute = ref.hour, ref.minute
        second, microsecond = ref.second, ref.microsecond

    return datetime(
        target_day.year,
        target_day.month,
        target_day.day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=TZ_TAIPEI,
    )


def resolve_observed_at(
    observed_at: str | None,
    raw_expr: str | None,
    reference: str | datetime,
) -> str:
    """決定事件的 `ts`。

    模型給的 `observed_at` 若是合法絕對時間就採用並正規化；否則退回用
    `raw_temporal_expression`（再退回 `observed_at` 原字串）配合參考時間推導。
    """
    if observed_at:
        try:
            return format_ts(parse_ts(observed_at))
        except TemporalError:
            pass
    return format_ts(resolve_expression(raw_expr or observed_at, reference))
