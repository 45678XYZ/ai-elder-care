"""例行公事 occurrence 的衍生（不保存狀態）。

規範見 docs/api.md 的「例行公事」與 docs/framework.md 的 `routines` 表。核心原則：
`routines` 表只存**不可變的版本化計畫**，occurrence 的 `done/pending/missed` 一律動態衍生，
真理來源是 `elder_id + routine_id + routine_date` 的 canonical completion event。

三條規則決定結果，順序不可換：

1. `occurrence_cutoff = min(查詢或摘要 cutoff, 該日台灣日界結束)`。歷史日期一旦過完，
   cutoff 就封頂在那一天，後續改版不得回頭改寫已成為過去的 occurrence。
2. **completion-first**：completion event 存在即為 `done`，顯示用的定義取 event 記錄的
   `routine_version` 對應版本。同日稍後改標題或改時間都不影響已完成的紀錄。
3. 未完成才展開排程，且只用 `occurrence_cutoff` 前最新的有效版本；每個
   `routine_id + date` 最多一筆 occurrence，同日改版是 supersede 而不是新增第二筆。

這一層同時服務每日摘要與未來的 `GET /routines?date=`，避免兩處各算一次而給出不同答案。
"""

from datetime import datetime, timedelta
from typing import Any
import logging
import os

from botocore.exceptions import ClientError

from src.extraction.temporal import TZ_TAIPEI, day_end, format_ts, parse_ts

from . import db

logger = logging.getLogger(__name__)

ROUTINE_VERSIONS_BY_ELDER_INDEX = "routine-versions-by-elder"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_MISSED = "missed"

FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"
FREQ_ONCE = "once"

# occurrence 由 pending 轉 missed 的寬限；routines、摘要與統計共用同一個值
DEFAULT_GRACE_MINUTES = 120

# 未指定時間的版本用這個時間展開，避免因缺欄位整筆消失
FALLBACK_TIME = "09:00"

# 摘要 `routines.items[]` 的固定欄位（docs/api.md）
SUMMARY_ITEM_FIELDS: tuple[str, ...] = ("routine_id", "title", "status")


class RoutineError(db.DBError):
    """routines 讀取或版本解析失敗。"""


def grace_minutes() -> int:
    raw = os.environ.get("ROUTINE_GRACE_MINUTES", "").strip()
    if not raw:
        return DEFAULT_GRACE_MINUTES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_GRACE_MINUTES


def occurrence_cutoff(date: str, cutoff: str | datetime | None = None) -> datetime:
    """`min(傳入 cutoff, 該日台灣日界結束)`。

    傳入 cutoff 才是「觀察到哪個時間點」的來源：當日查詢傳現在時間，摘要傳
    `input_through_at`。封頂在日界結束是為了讓歷史日期的結果穩定。
    """
    day_boundary = parse_ts(day_end(date))
    if cutoff is None:
        moment = datetime.now(TZ_TAIPEI)
    else:
        moment = parse_ts(cutoff)
    return min(moment, day_boundary)


# -----------------------------------------------------------------------------
# routines 表讀取
# -----------------------------------------------------------------------------


def get_routine_version(routine_id: str, version: int) -> dict[str, Any] | None:
    """取特定版本的不可變定義；不存在回 None。"""
    table = db.get_dynamodb_resource().Table(db.TABLE_ROUTINES)
    try:
        response = table.get_item(Key={"routine_id": routine_id, "version": int(version)})
    except ClientError as exc:
        raise RoutineError(f"讀取 routine 版本失敗: {exc.response['Error']['Message']}")
    item = response.get("Item")
    return db.convert_decimals(item) if item else None


def list_versions_effective_by(elder_id: str, cutoff: datetime) -> list[dict[str, Any]]:
    """取 `effective_from` 不晚於 cutoff 的所有版本，走 `routine-versions-by-elder`。

    不用 `routines-current-by-elder`：那個 sparse GSI 只有 current 版本，拿它回推歷史日期
    會把今天的定義套到過去，違反「不得 retroactively 改寫」。
    """
    upper_bound = format_ts(cutoff) + db.TIME_KEY_UPPER_BOUND_SUFFIX
    query: dict[str, Any] = {
        "IndexName": ROUTINE_VERSIONS_BY_ELDER_INDEX,
        "KeyConditionExpression": "elder_id = :eid AND version_time_key <= :upper",
        "ExpressionAttributeValues": {":eid": elder_id, ":upper": upper_bound},
        "ScanIndexForward": True,
    }
    table = db.get_dynamodb_resource().Table(db.TABLE_ROUTINES)
    versions: list[dict[str, Any]] = []
    while True:
        try:
            response = table.query(**query)
        except ClientError as exc:
            raise RoutineError(f"查詢 routine 版本失敗: {exc.response['Error']['Message']}")
        versions.extend(db.convert_decimals(response.get("Items", [])))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return versions
        query["ExclusiveStartKey"] = last_key


def latest_versions_by_routine(versions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """依 `routine_id` 收斂成 cutoff 前最新的有效版本。

    排序鍵用 `(effective_from, version)`：同一毫秒建立兩個版本時 `version` 才是決勝欄位。
    """
    latest: dict[str, dict[str, Any]] = {}
    for item in versions:
        routine_id = item.get("routine_id")
        if not routine_id:
            continue
        current = latest.get(routine_id)
        key = (str(item.get("effective_from") or ""), int(item.get("version") or 0))
        if current is None or key > (
            str(current.get("effective_from") or ""),
            int(current.get("version") or 0),
        ):
            latest[routine_id] = item
    return latest


def list_completion_events(elder_id: str, date: str, *, page_size: int = 100) -> dict[str, dict[str, Any]]:
    """取該日的 canonical routine completion event，依 `routine_id` 索引。

    completion event 必帶 `routine_id` 與 `routine_date`（framework 的 events 欄位表），
    batch 萃取到的疑似完成只寫在 `structured_detail.suspected_routine_id`，因此不會誤入。
    """
    completions: dict[str, dict[str, Any]] = {}
    next_token: str | None = None
    while True:
        items, next_token = db.list_events(
            elder_id, from_date=date, to_date=date, limit=page_size, next_token=next_token
        )
        for item in items:
            routine_id = item.get("routine_id")
            if not routine_id or item.get("routine_date") != date:
                continue
            # 同一 canonical key 只會有一筆；真出現多筆時保留最早寫入的那筆
            completions.setdefault(routine_id, item)
        if not next_token:
            return completions


# -----------------------------------------------------------------------------
# occurrence 衍生
# -----------------------------------------------------------------------------


def is_scheduled_on(schedule: dict[str, Any], date: str) -> bool:
    """該版本的排程是否落在這一天。每種 freq 每天最多一次。"""
    freq = schedule.get("freq")
    if freq == FREQ_DAILY:
        return True
    if freq == FREQ_WEEKLY:
        # weekday 1–7，週一為 1；一筆 weekly routine 只帶單一 weekday
        return schedule.get("weekday") == parse_ts(f"{date}T00:00:00+08:00").isoweekday()
    if freq == FREQ_ONCE:
        return schedule.get("date") == date
    return False


def scheduled_at_for(version: dict[str, Any], date: str) -> str:
    time_of_day = (version.get("schedule") or {}).get("time") or FALLBACK_TIME
    return f"{date}T{time_of_day}:00.000+08:00"


def list_occurrences(
    elder_id: str,
    date: str,
    *,
    cutoff: str | datetime | None = None,
    grace: int | None = None,
) -> list[dict[str, Any]]:
    """展開某一天的 occurrence 快照，依 `scheduled_at` 正序。"""
    limit = occurrence_cutoff(date, cutoff)
    grace_delta = timedelta(minutes=grace if grace is not None else grace_minutes())

    completions = list_completion_events(elder_id, date)
    latest = latest_versions_by_routine(list_versions_effective_by(elder_id, limit))

    occurrences: list[dict[str, Any]] = []

    for routine_id, event in completions.items():
        definition = _definition_for_completion(routine_id, event, latest.get(routine_id))
        occurrence = {
            "routine_id": routine_id,
            "title": (definition or {}).get("title") or "",
            "type": (definition or {}).get("type") or event.get("type") or "other",
            "scheduled_at": (
                scheduled_at_for(definition, date) if definition else event.get("ts")
            ),
            "status": STATUS_DONE,
            "completed_at": event.get("ts"),
            "completed_by": event.get("completed_by") or "conversation",
        }
        occurrences.append(occurrence)

    for routine_id, version in latest.items():
        if routine_id in completions:
            continue
        if not version.get("active", True):
            # 停用後的最新版本不再產生 occurrence；已完成的那些走上面的 completion 路徑
            continue
        if not is_scheduled_on(version.get("schedule") or {}, date):
            continue
        scheduled_at = scheduled_at_for(version, date)
        deadline = parse_ts(scheduled_at) + grace_delta
        occurrences.append(
            {
                "routine_id": routine_id,
                "title": version.get("title") or "",
                "type": version.get("type") or "other",
                "scheduled_at": scheduled_at,
                "status": STATUS_MISSED if limit > deadline else STATUS_PENDING,
            }
        )

    occurrences.sort(key=lambda item: (item.get("scheduled_at") or "", item["routine_id"]))
    return occurrences


def _definition_for_completion(
    routine_id: str,
    event: dict[str, Any],
    fallback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """已完成 occurrence 的顯示定義：優先取 event 記錄的版本。

    `routine_version` 只記錄「完成當下採用的版本」，不參與 identity；拿它讀回不可變版本，
    才能在同日改版後仍呈現完成當時看到的標題與時間。版本讀不到時退回 cutoff 前最新版本，
    並記錄下來——那代表資料被刪過，不該讓整份摘要生成失敗。
    """
    version_number = event.get("routine_version")
    if version_number is not None:
        definition = get_routine_version(routine_id, int(version_number))
        if definition:
            return definition
        logger.warning(
            "completion event 指向的 routine 版本不存在：routine_id=%s version=%s",
            routine_id,
            version_number,
        )
    return fallback


def summary_snapshot(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """摘要用的 `routines` 區塊：固定 `{completed, missed, items[{routine_id,title,status}]}`。

    `pending` 不計入 `completed` 也不計入 `missed`（docs/api.md），但仍出現在 `items`，
    照護者才看得到「今天還有什麼沒做」。
    """
    return {
        "completed": sum(1 for item in occurrences if item["status"] == STATUS_DONE),
        "missed": sum(1 for item in occurrences if item["status"] == STATUS_MISSED),
        "items": [
            {field: item.get(field) for field in SUMMARY_ITEM_FIELDS} for item in occurrences
        ],
    }
