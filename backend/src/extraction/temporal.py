"""時間正規化與相對時間解析模組。

提供將自然語言相對時間表達（如「昨天早上」、「大前天下午三點」）轉譯為標準 ISO 8601
絕對時間戳，並進行台灣時區 (+08:00) 與固定毫秒精度正規化。
架構規範與 DynamoDB `events` 資料表 `event_time_key` 設計詳見 `docs/framework.md`。

本模組設計目的與核心機制：
- **固定毫秒精度與台灣時區 (+08:00)**：`ts` 字串會寫入 DynamoDB 作為 `event_time_key` (`SK`)。在 DynamoDB 中，字串排序即時間排序；若毫秒位數不一致（如有的帶毫秒、有的不帶），會導致 SK 字典排序與實際時間點錯亂。
- **參考時間 mandatory 要求（嚴禁 `datetime.now()`）**：批次任務 (Batch Job) 可能因 SQS Retry、重覆派送或 DLQ Replay 延後數天執行。若參考時間隨執行當下時間漂移，同一段對話將算出不同的時間戳與 Canonical Key，使冪等性徹底破功。參考時間一律取自對話輪次的 `created_at`。
- **台灣日界統一計算**：每日摘要與例行公事追溯皆以台灣當地下午與半夜日界（+08:00 的 00:00 至 23:59）為判斷標準。
"""

from datetime import date, datetime, timedelta, timezone
import re
import unicodedata

# 台灣標準時區 (+08:00)；全系統事件時間儲存與每日摘要劃分之唯一基準時區
TZ_TAIPEI = timezone(timedelta(hours=8), name="+08:00")

# 相對時間時段對照表；當對話僅提到時段（如「早上」）而無明確幾點幾分時，提供符合長者日常作息之代表時刻
_PERIOD_HOURS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("凌晨", "深夜", "半夜"), 3),
    (("清晨", "早晨", "早上", "上午", "今早"), 8),
    (("中午", "正午"), 12),
    (("下午", "午後", "過午"), 14),
    (("傍晚",), 18),
    # 「昨晚」「今晚」本身即帶時段語意，需包含於關鍵字比對中
    (("晚上", "夜間", "夜裡", "晚間", "昨晚", "今晚", "昨夜"), 20),
)

# 相對日期對照表；陣列順序極具意義，字串較長者（如「大前天」）必須排在較短者（如「前天」）之前比對，防止子字串誤判
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

# 中文數字至整數映射表；用於解析「三天前」、「兩點半」等中文數字時間表達
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
    """相對或絕對時間字串格式錯誤、無效或無法解析時拋出之例外。"""


def _to_int_zh(token: str) -> int | None:
    """將阿拉伯數字或中文數字標記轉換為整數。"""
    if token.isdigit():
        return int(token)
    return _ZH_NUMERALS.get(token)


def to_taipei(value: datetime) -> datetime:
    """將 datetime 物件統一轉換為台灣時區 (+08:00)。

    若傳入之 datetime 為 naive（無時區資訊），預設將其視為台灣本地時間，
    避免未帶時區的 timestamp 被系統誤認為 UTC 造成 8 小時時差偏差。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ_TAIPEI)
    return value.astimezone(TZ_TAIPEI)


def parse_ts(value: str | datetime) -> datetime:
    """將 ISO 8601 字串或 datetime 物件解析為帶有台灣時區 (+08:00) 的 datetime 物件。

    對結尾帶有 'Z' 或 'z' 的 UTC 字串進行標準化替換，確保跨系統傳播的時間字串均能正確轉譯。
    """
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
    """輸出固定 3 位毫秒精度與 +08:00 時區標記的標準 ISO 8601 時間字串。

    強制的格式固定 (`YYYY-MM-DDTHH:MM:SS.mmm+08:00`) 是為了確保寫入 DynamoDB 
    的 `event_time_key` 在執行 SK 字典順序排序時，字串排序能完全等同於真實時間先後排序。
    """
    taipei = to_taipei(value)
    milliseconds = taipei.microsecond // 1000
    return f"{taipei:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}+08:00"


def normalize_ts(value: str | datetime) -> str:
    """將任意合法時間表示統一正規化為 `event_time_key` 使用的標準字串格式。"""
    return format_ts(parse_ts(value))


def day_key(value: str | datetime) -> str:
    """計算台灣日界下的日期字串 (`YYYY-MM-DD`)。

    用於作為 DynamoDB `events` 與 `daily_summaries` 表的日期 Partition Key。
    """
    return f"{parse_ts(value):%Y-%m-%d}"


def day_start(day: str) -> str:
    """計算指定日期在台灣時區下的當日起點時間戳 (`YYYY-MM-DD T00:00:00.000+08:00`)。"""
    return f"{day}T00:00:00.000+08:00"


def day_end(day: str) -> str:
    """計算指定日期在台灣時區下的當日終點時間戳 (`YYYY-MM-DD T23:59:59.999+08:00`)。

    用於時間範圍查詢（GSI Query）的上界，以及計算例行公事（routines）未完成裁定的邊界點。
    """
    return f"{day}T23:59:59.999+08:00"


def _match_day_offset(expr: str) -> int | None:
    """從自然語言中比對相對日期的位移天數（如「昨天」為 -1，「大前天」為 -3）。"""
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
    """從自然語言中比對時段廣義小時數（如「早上」對應 8 點，「晚上」對應 20 點）。"""
    for keywords, hour in _PERIOD_HOURS:
        if any(keyword in expr for keyword in keywords):
            return hour
    return None


def _match_explicit_time(expr: str) -> tuple[int, int] | None:
    """從自然語言中比對具體鐘點與分鐘數（如「15:30」、「三點半」）。"""
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
    """將對話中的相對時間表達轉譯為精確的絕對時間 datetime 物件。

    `reference` 為必須傳入的參考基準時間（取自對話輪次的 `created_at`）。
    確定性的參考時間能保證相同的相對時間輸入恆得到相同的絕對時間產出，為批次任務重試與冪等性的重要基礎。
    當僅有日期而缺乏具體時刻時，預設沿用參考時間之時刻，避免盲目捏造為 00:00。
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
    """決定萃取事件最終使用的絕對時間戳 `ts`。

    優先採用 LLM 模型輸出的 `observed_at`（若為合法 ISO 8601 時間字串則標準化輸出）；
    若模型給出的時間非法或為空，則退回使用相對時間表達 `raw_temporal_expression` 搭配參考時間推導。
    """
    if observed_at:
        try:
            return format_ts(parse_ts(observed_at))
        except TemporalError:
            pass
    return format_ts(resolve_expression(raw_expr or observed_at, reference))
