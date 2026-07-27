"""每日摘要的排程生成（EventBridge）。

兩種觸發，共用 `shared/summarizer.py` 的生成邏輯（規範見 docs/api.md 的每日摘要與
docs/feature_daily-summarization.md §7）：

- `mode=nightly`：台灣時間深夜跑一次當日摘要。此時仍有未完成 batch 的 session 就寫
  `partial`，不卡著等——照護者當晚就要看得到已知的部分。
- `mode=backfill`：掃近幾天的 `partial` 摘要重算。相關 batch 完成後，這一輪會把它覆寫成
  `complete`；覆寫優先序由資料層的條件式寫入保證，這裡不做讀後判斷。

等待窗口的實作是「只重算 `generated_at` 距今在 `SUMMARY_WAIT_MINUTES` 內的 partial」。
超過窗口就放手：batch 若真的卡在 `failed`，那是 DLQ reconciler 與告警的責任，
不該讓摘要無限重算燒模型費用。
"""

from datetime import datetime, timedelta
from typing import Any
import logging
import os

from src.extraction.temporal import TZ_TAIPEI, day_key, parse_ts
from src.shared import bedrock, db, metrics, summarizer

logger = logging.getLogger(__name__)

MODE_NIGHTLY = "nightly"
MODE_BACKFILL = "backfill"

DEFAULT_WAIT_MINUTES = 180
DEFAULT_BACKFILL_DAYS = 2
DEFAULT_SWEEP_LIMIT = 50


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def wait_minutes() -> int:
    return _env_int("SUMMARY_WAIT_MINUTES", DEFAULT_WAIT_MINUTES)


def backfill_days() -> int:
    return _env_int("SUMMARY_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS)


def sweep_limit() -> int:
    return _env_int("SUMMARY_SWEEP_LIMIT", DEFAULT_SWEEP_LIMIT)


def handler(event, context):
    """EventBridge 入口。`mode` 由 rule 的 input 指定，預設 nightly。"""
    payload = event or {}
    mode = str(payload.get("mode") or MODE_NIGHTLY).lower()
    if mode == MODE_BACKFILL:
        return run_backfill(payload)
    return run_nightly(payload)


# -----------------------------------------------------------------------------
# nightly
# -----------------------------------------------------------------------------


def run_nightly(payload: dict[str, Any]) -> dict[str, Any]:
    """對每位長者生成指定日期（預設今天）的摘要。"""
    now = datetime.now(TZ_TAIPEI)
    date = str(payload.get("date") or day_key(now))
    elders = _target_elders(payload)

    generated = 0
    partial = 0
    failed = 0
    for elder_id in elders:
        try:
            summary, _ = summarizer.generate_and_store(elder_id, date)
        except (bedrock.BedrockError, db.DBError):
            # 單一長者失敗不影響其他人；下一輪 backfill 或隔天排程會再處理
            logger.exception("排程摘要生成失敗：elder_id=%s date=%s", elder_id, date)
            failed += 1
            continue
        generated += 1
        if summary.get("data_status") == summarizer.DATA_STATUS_PARTIAL:
            partial += 1

    result = {
        "mode": MODE_NIGHTLY,
        "date": date,
        "elders": len(elders),
        "generated": generated,
        "partial": partial,
        "failed": failed,
    }
    logger.info("排程摘要完成：%s", result)
    return result


# -----------------------------------------------------------------------------
# backfill
# -----------------------------------------------------------------------------


def run_backfill(payload: dict[str, Any]) -> dict[str, Any]:
    """重算等待窗口內仍為 `partial` 的摘要。"""
    now = datetime.now(TZ_TAIPEI)
    days = int(payload.get("days") or backfill_days())
    window = timedelta(minutes=wait_minutes())
    to_date = day_key(now)
    from_date = day_key(now - timedelta(days=max(days - 1, 0)))

    elders = _target_elders(payload)
    regenerated = 0
    upgraded = 0
    skipped_stale = 0
    failed = 0

    for elder_id in elders:
        try:
            summaries, _ = db.list_daily_summaries(
                elder_id, from_date=from_date, to_date=to_date, limit=days + 1
            )
        except db.DBError:
            logger.exception("讀取待重算摘要失敗：elder_id=%s", elder_id)
            failed += 1
            continue

        for summary in summaries:
            if summary.get("data_status") != summarizer.DATA_STATUS_PARTIAL:
                continue
            if not _within_window(summary, now=now, window=window):
                # 超過等待窗口：停止重算，交給 batch 告警處理
                skipped_stale += 1
                continue
            date = summary["date"]
            try:
                refreshed, written = summarizer.generate_and_store(elder_id, date)
            except (bedrock.BedrockError, db.DBError):
                logger.exception("摘要重算失敗：elder_id=%s date=%s", elder_id, date)
                failed += 1
                continue
            regenerated += 1
            if written and refreshed.get("data_status") == summarizer.DATA_STATUS_COMPLETE:
                upgraded += 1

    result = {
        "mode": MODE_BACKFILL,
        "from": from_date,
        "to": to_date,
        "elders": len(elders),
        "regenerated": regenerated,
        "upgraded": upgraded,
        "skipped_stale": skipped_stale,
        "failed": failed,
    }
    metrics.emit(
        {metrics.SUMMARY_REGENERATED: regenerated},
        dimensions={"Outcome": "upgraded" if upgraded else "unchanged"},
    )
    logger.info("摘要重算 sweep 完成：%s", result)
    return result


def _within_window(summary: dict[str, Any], *, now: datetime, window: timedelta) -> bool:
    """`generated_at` 是否還在等待窗口內。

    缺 `generated_at` 的資料視為過期而不是重算：那代表資料異常，重算也很可能再失敗。
    """
    generated_at = summary.get("generated_at")
    if not generated_at:
        return False
    try:
        return parse_ts(generated_at) >= now - window
    except Exception:
        logger.warning(
            "摘要的 generated_at 無法解析，跳過重算：elder_id=%s date=%s value=%s",
            summary.get("elder_id"),
            summary.get("date"),
            generated_at,
        )
        return False


def _target_elders(payload: dict[str, Any]) -> list[str]:
    """要處理的長者清單。

    `elder_ids` 可由手動觸發指定（重跑單一長者用）；否則掃 `elders` 表。MVP 的長者數在
    數十量級，`scan` 足夠；單次處理量仍受 `SUMMARY_SWEEP_LIMIT` 約束，避免 Lambda 超時。
    """
    explicit = payload.get("elder_ids")
    if explicit:
        return [str(item) for item in explicit][: sweep_limit()]
    try:
        elders = db.list_elders()
    except db.DBError:
        logger.exception("讀取長者清單失敗，本次 sweep 不處理")
        return []
    return [item["elder_id"] for item in elders if item.get("elder_id")][: sweep_limit()]
