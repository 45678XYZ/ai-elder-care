"""GET /stats — 互動與行程統計 API。規格見 docs/api.md。

處理流程：
1. 權限與參數校驗：確認呼叫者可存取該長者，並限制 days 範圍（區間為台灣日界下含今天的最近 N 天）
2. 互動統計：從 conversations 取期間內已完成 turn 的時間戳，聚合為逐日輪數
   （`interaction_count` 一律指 /chat 對話輪數，不是 session 數）
3. 行程統計：一次取回區間內有效的 routine 版本與 canonical completion events，
   再逐日以 src/shared/routines.py 的 completion-first 規則推導 occurrence
4. 組裝：today 即時值、period 區間彙總、routines.by_routine 逐項完成度、daily 逐日趨勢
"""

from collections import Counter
from datetime import datetime, timedelta
import logging
from typing import Any

from src.extraction.temporal import day_key
from src.shared import auth, db, responses, routines
from src.shared.models import (
    StatsDailyPoint,
    StatsPeriod,
    StatsResponse,
    StatsRoutineItem,
    StatsRoutines,
    StatsToday,
)

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 7
# 統計一律即時彙總，區間越長就要展開越多天的 occurrence 與 completion event；
# 以一個月為上界，避免單次查詢把 Lambda 拖到逾時
MAX_DAYS = 31

STATUS_DONE = "done"


def handler(event, context):
    """GET /stats 入口；解析查詢參數、校驗權限並回傳互動與行程統計。"""
    params = event.get("queryStringParameters") or {}

    elder_id = (params.get("elder_id") or "").strip()
    if not elder_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 elder_id")

    try:
        auth.assert_can_access_elder(event, elder_id)
    except auth.AuthError as exc:
        return exc.response

    raw_days = (params.get("days") or "").strip()
    days = DEFAULT_DAYS
    if raw_days:
        if not raw_days.isdigit() or not 1 <= int(raw_days) <= MAX_DAYS:
            return responses.error(400, "INVALID_PARAMETER", f"days 須為 1–{MAX_DAYS}")
        days = int(raw_days)

    current = routines.now()
    dates = _period_dates(current, days)

    try:
        turn_times = db.list_turn_times(elder_id, from_date=dates[0], to_date=dates[-1])
        occurrences = _occurrences_by_date(elder_id, dates, current)
    except db.DBError:
        logger.exception("查詢統計資料失敗：elder_id=%s days=%s", elder_id, days)
        return responses.error(500, "INTERNAL_ERROR", "查詢統計失敗")

    return responses.json_response(
        200, _to_stats(elder_id, dates, turn_times, occurrences)
    )


def _period_dates(current: datetime, days: int) -> list[str]:
    """統計區間的日期清單（台灣日界、遞增，最後一天為今天）。"""
    last_day = datetime.strptime(routines.today(current), "%Y-%m-%d").date()
    first_day = last_day - timedelta(days=days - 1)
    return [
        (first_day + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days)
    ]


def _occurrences_by_date(
    elder_id: str, dates: list[str], current: datetime
) -> dict[str, list[dict[str, Any]]]:
    """逐日推導 occurrence，回 {date: [occurrence, ...]}。

    版本只查一次：上界取區間最後一天的 `occurrence_cutoff`，較早的日期由
    `resolve_occurrences` 依各自 cutoff 收斂，因此不必為每一天各查一次歷史版本。
    """
    upper_bound = routines.versions_upper_bound(
        routines.occurrence_cutoff(dates[-1], current)
    )
    versions = db.list_routine_versions_by_elder(elder_id, upper_bound)
    if not versions:
        return {date: [] for date in dates}

    completions = _completion_events(elder_id, versions, dates, current)
    return {
        date: routines.resolve_occurrences(versions, completions[date], date, current)
        for date in dates
    }


def _completion_events(
    elder_id: str, versions: list[dict[str, Any]], dates: list[str], current: datetime
) -> dict[str, dict[str, dict[str, Any]]]:
    """批次取回區間內每一天各 routine 的 canonical completion event。

    event_id 由 canonical key 穩定產生，因此整個區間可一次批次取回，不必掃事件時間軸——
    完成時間落在隔日的補登也會命中同一 occurrence（與 GET /routines 同一套規則）。
    """
    by_event_id: dict[str, tuple[str, str]] = {}
    for date in dates:
        cutoff = routines.occurrence_cutoff(date, current)
        for routine_id in routines.routine_ids_effective_by(versions, cutoff):
            event_id = db.event_id_for(
                elder_id, routines.completion_event_key(routine_id, date)
            )
            by_event_id[event_id] = (date, routine_id)

    completions: dict[str, dict[str, dict[str, Any]]] = {date: {} for date in dates}
    if not by_event_id:
        return completions

    for event_id, item in db.get_events(elder_id, list(by_event_id)).items():
        date, routine_id = by_event_id[event_id]
        completions[date][routine_id] = item
    return completions


def _to_stats(
    elder_id: str,
    dates: list[str],
    turn_times: list[str],
    occurrences: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """彙總為 api.md 規範的統計物件；無互動時 last_interaction_at 省略不回 null。"""
    turns_by_day = [(day_key(ts), ts) for ts in turn_times]
    counts = Counter(day for day, _ in turns_by_day)
    today = dates[-1]
    today_times = [ts for day, ts in turns_by_day if day == today]

    return StatsResponse(
        elder_id=elder_id,
        today=StatsToday(
            interaction_count=len(today_times),
            last_interaction_at=max(today_times, default=None),
        ),
        period=StatsPeriod(
            days=len(dates),
            interaction_count=sum(counts[date] for date in dates),
            active_days=sum(1 for date in dates if counts[date]),
        ),
        routines=StatsRoutines(by_routine=_by_routine(dates, occurrences)),
        daily=[
            StatsDailyPoint(
                date=date,
                interaction_count=counts[date],
                routines_completed=sum(
                    1 for item in occurrences[date] if item["status"] == STATUS_DONE
                ),
                routines_total=len(occurrences[date]),
            )
            for date in dates
        ],
    ).model_dump(exclude_none=True)


def _by_routine(
    dates: list[str], occurrences: dict[str, list[dict[str, Any]]]
) -> list[StatsRoutineItem]:
    """逐 routine 累計區間內的排程與完成次數；沒排程過的 routine 不出現。"""
    totals: dict[str, dict[str, Any]] = {}
    for date in dates:
        for item in occurrences[date]:
            entry = totals.setdefault(item["routine_id"], {"completed": 0, "total": 0})
            # 日期遞增，因此標題最後會停在區間內最新一次排程採用的版本
            entry["title"] = item["title"]
            entry["total"] += 1
            if item["status"] == STATUS_DONE:
                entry["completed"] += 1
    return [
        StatsRoutineItem(routine_id=routine_id, **totals[routine_id])
        for routine_id in sorted(totals)
    ]
