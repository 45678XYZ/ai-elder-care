"""例行公事領域邏輯（純運算，不接觸 AWS）。規格見 docs/api.md 與 docs/framework.md。

- **版本不可變**：每次修改產生新版本，只有最新版本為 current；歷史日期沿用當時有效版本，
  後續改版不得回頭改寫既有日期的 occurrence
- **occurrence 狀態不落地**：以 canonical completion event、`occurrence_cutoff` 與寬限期
  即時推導 `done`／`missed`／`pending`，routines 表不保存完成狀態
- **冪等鍵**：`routine_id`、`change_request_id` 與 `request_hash` 都由 scoped 輸入穩定產生，
  相同請求重送收斂到同一結果而不產生新版本
"""
from datetime import datetime, timedelta
import hashlib
import json
import os
from typing import Any

from src.extraction.canonical import routine_completion_key
from src.shared.db import TZ_TAIPEI

# 未完成 occurrence 超過 scheduled_at 加此寬限期才算 missed；routines、摘要與統計共用
GRACE_MINUTES = int(os.environ.get("ROUTINE_GRACE_MINUTES", "120"))

# 不同組合雜湊出相同字串會造成身分碰撞，因此以不可能出現在 ID 中的控制字元分隔
_ID_SEPARATOR = "\x1f"

# version_time_key 的上界：讓 effective_from 恰等於 cutoff 的版本也落在查詢區間內
_MAX_SORT_SUFFIX = "#\uffff"


# -----------------------------------------------------------------------------
# 時間與日期
# -----------------------------------------------------------------------------

def now() -> datetime:
    """目前台灣時間。"""
    return datetime.now(TZ_TAIPEI)


def to_iso(dt: datetime) -> str:
    """固定毫秒精度的台灣時間 ISO 8601，供 effective_from 等排序鍵使用。"""
    return dt.astimezone(TZ_TAIPEI).isoformat(timespec="milliseconds")


def now_iso() -> str:
    return to_iso(now())


def today(current: datetime | None = None) -> str:
    """台灣日界下的今天（YYYY-MM-DD）。"""
    return (current or now()).astimezone(TZ_TAIPEI).strftime("%Y-%m-%d")


def is_valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def occurrence_cutoff(date_str: str, current: datetime) -> datetime:
    """`min(查詢時間, 該日台灣日界結束)`；歷史日期越過日界後即封頂於該日。"""
    day_end = datetime.fromisoformat(f"{date_str}T23:59:59.999+08:00")
    return min(current, day_end)


# -----------------------------------------------------------------------------
# 排序鍵與冪等鍵
# -----------------------------------------------------------------------------

def current_sort_key(active: bool, created_at: str, routine_id: str) -> str:
    """sparse GSI `routines-current-by-elder` 的排序鍵；只有 current 版本帶此欄位。"""
    return f"{'A' if active else 'I'}#{created_at}#{routine_id}"


def version_time_key(effective_from: str, routine_id: str, version: int) -> str:
    """GSI `routine-versions-by-elder` 的排序鍵。"""
    return f"{effective_from}#{routine_id}#{version}"


def versions_upper_bound(cutoff: datetime) -> str:
    """查 cutoff 前所有版本時的 `version_time_key` 上界。"""
    return f"{to_iso(cutoff)}{_MAX_SORT_SUFFIX}"


def stable_id(prefix: str, *parts: str) -> str:
    """由 scoped 輸入穩定產生 ID：相同輸入永遠得到相同 ID。"""
    raw = _ID_SEPARATOR.join(parts).encode("utf-8")
    return f"{prefix}{hashlib.sha256(raw).hexdigest()[:12]}"


def request_hash(payload: dict[str, Any]) -> str:
    """正規化 payload 的 hash，供同一冪等 scope 判斷是重送或衝突。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def completion_event_key(routine_id: str, routine_date: str) -> str:
    """occurrence 的 canonical 事件身分；對話與手動完成都收斂到同一筆 event。

    key 的組法只有一份實作（src/extraction/canonical.py），realtime 與 batch 兩條軌
    各自組字串就會漂移成兩筆事件。
    """
    return routine_completion_key(routine_id, routine_date)


# -----------------------------------------------------------------------------
# 版本收斂與 occurrence 推導
# -----------------------------------------------------------------------------

def select_effective_version(
    versions: list[dict[str, Any]], cutoff: datetime
) -> dict[str, Any] | None:
    """cutoff 前最新的有效版本；沒有任何版本生效時回 None。"""
    candidates = [v for v in versions if datetime.fromisoformat(v["effective_from"]) <= cutoff]
    if not candidates:
        return None
    return max(candidates, key=lambda v: (v["effective_from"], int(v["version"])))


def routine_ids_effective_by(versions: list[dict[str, Any]], cutoff: datetime) -> set[str]:
    """cutoff 前已有版本生效的 routine ID。

    供逐日展開多天區間時，只為當日已存在的 routine 算 completion event key；尚未建立的
    routine 不必白算一組 key 去批次讀取。
    """
    return {
        version["routine_id"]
        for version in versions
        if datetime.fromisoformat(version["effective_from"]) <= cutoff
    }


def scheduled_at(date_str: str, schedule: dict[str, Any]) -> str | None:
    """該版本在指定日期的排定時間；該日無排程回 None。"""
    time_str = schedule.get("time")
    if not time_str:
        return None

    freq = schedule.get("freq")
    if freq == "daily":
        matched = True
    elif freq == "weekly":
        weekday = schedule.get("weekday")
        matched = weekday is not None and int(weekday) == _isoweekday(date_str)
    elif freq == "once":
        matched = schedule.get("date") == date_str
    else:
        matched = False

    return f"{date_str}T{time_str}:00+08:00" if matched else None


def resolve_occurrence(
    versions: list[dict[str, Any]],
    completion_event: dict[str, Any] | None,
    date_str: str,
    current: datetime,
    grace_minutes: int | None = None,
) -> dict[str, Any] | None:
    """單一 routine 在指定日期的唯一 occurrence；該日無排程回 None。

    completion-first：已有 completion event 就固定為 `done`，顯示資料取事件所記版本，
    即使同日稍後改版也保留完成當時的結果。
    """
    cutoff = occurrence_cutoff(date_str, current)

    if completion_event:
        version = _find_version(versions, completion_event.get("routine_version"))
        version = version or select_effective_version(versions, cutoff)
        if version is None:
            return None
        occurrence = _display_fields(version, date_str)
        # 完成後才改動排程時，該版本可能已不落在本日；仍以完成時間呈現這筆 occurrence
        occurrence["scheduled_at"] = occurrence["scheduled_at"] or completion_event.get("ts")
        occurrence["status"] = "done"
        occurrence["completed_at"] = completion_event.get("ts")
        occurrence["completed_by"] = completion_event.get("completed_by", "conversation")
        return occurrence

    version = select_effective_version(versions, cutoff)
    if version is None or not version.get("active", True):
        return None
    occurrence = _display_fields(version, date_str)
    if occurrence["scheduled_at"] is None:
        return None

    grace = GRACE_MINUTES if grace_minutes is None else grace_minutes
    deadline = datetime.fromisoformat(occurrence["scheduled_at"]) + timedelta(minutes=grace)
    occurrence["status"] = "missed" if current > deadline else "pending"
    return occurrence


def resolve_occurrences(
    versions: list[dict[str, Any]],
    completion_events: dict[str, dict[str, Any]],
    date_str: str,
    current: datetime,
    grace_minutes: int | None = None,
) -> list[dict[str, Any]]:
    """指定日期的完整行程視圖，依 scheduled_at 排序；每個 routine 最多一筆。"""
    by_routine: dict[str, list[dict[str, Any]]] = {}
    for version in versions:
        by_routine.setdefault(version["routine_id"], []).append(version)

    items = []
    for routine_id, routine_versions in by_routine.items():
        occurrence = resolve_occurrence(
            routine_versions,
            completion_events.get(routine_id),
            date_str,
            current,
            grace_minutes,
        )
        if occurrence:
            items.append(occurrence)

    items.sort(key=lambda item: (item["scheduled_at"] or "", item["routine_id"]))
    return items


def _display_fields(version: dict[str, Any], date_str: str) -> dict[str, Any]:
    return {
        "routine_id": version["routine_id"],
        "title": version.get("title", ""),
        "type": version.get("type", "other"),
        "scheduled_at": scheduled_at(date_str, version.get("schedule") or {}),
    }


def _find_version(versions: list[dict[str, Any]], version_no: Any) -> dict[str, Any] | None:
    if version_no is None:
        return None
    return next((v for v in versions if int(v["version"]) == int(version_no)), None)


def _isoweekday(date_str: str) -> int:
    """週一為 1、週日為 7，與 schedule.weekday 同一套定義。"""
    return datetime.strptime(date_str, "%Y-%m-%d").isoweekday()
