"""EventBridge 排程每日摘要生成 Handler。規範見 docs/framework.md 與 docs/feature_daily-summarization.md。

處理途徑與模式：
1. mode=nightly（深夜當日摘要生成）：
   - 深夜定時執行，為每位長者產出當日摘要
   - 若相關對話之 batch 尚未處理完成，仍產出並寫入 data_status=partial 摘要，優先確保照護者當晚能看到已完成部分

2. mode=backfill（等待視窗重算補齊）：
   - 掃描近幾天內 data_status=partial 之摘要並重新生成
   - 相關 batch 完成後升級寫入為 complete（由資料層條件式寫入控制覆寫優先序）
   - 超過 SUMMARY_WAIT_MINUTES 等待窗口者放棄重算，防止卡住的 failed batch 造成無限重算並浪費 LLM 費用
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
    """從環境變數讀取整數值；若未設定或無效則回傳預設值。"""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def wait_minutes() -> int:
    """取得 partial 摘要允許重算升級的等待窗口時間（分鐘）。"""
    return _env_int("SUMMARY_WAIT_MINUTES", DEFAULT_WAIT_MINUTES)


def backfill_days() -> int:
    """取得 backfill 模式掃描歷史摘要的天數範圍。"""
    return _env_int("SUMMARY_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS)


def sweep_limit() -> int:
    """取得單次排程執行處理長者的上限數量，防止 Lambda 超時。"""
    return _env_int("SUMMARY_SWEEP_LIMIT", DEFAULT_SWEEP_LIMIT)


def handler(event, context):
    """EventBridge 排程觸發入口；依據 event payload 之 mode 參數分派執行。"""
    payload = event or {}
    mode = str(payload.get("mode") or MODE_NIGHTLY).lower()
    if mode == MODE_BACKFILL:
        return run_backfill(payload)
    return run_nightly(payload)


# -----------------------------------------------------------------------------
# nightly
# -----------------------------------------------------------------------------


def run_nightly(payload: dict[str, Any]) -> dict[str, Any]:
    """執行 nightly 模式，生成所有長者指定日期（預設今日）之摘要。"""
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
            # 單一長者生成失敗記錄例外並跳過，不中斷整體排程；未完成者留待下一輪 backfill 或隔天排程處理
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
    """執行 backfill 模式，重算並嘗試升級等待窗口內仍為 partial 的歷史摘要。"""
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
                # 超過等待窗口時間（SUMMARY_WAIT_MINUTES）：停止無謂重算以節省 LLM 成本，問題交由 DLQ 告警追蹤
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
    """判斷摘要之 generated_at 時間戳記是否仍位於允許重算的等待窗口內。

    缺 generated_at 或時間格式異常時直接判定為不在窗口內（視為異常資料），避免因無效資料反覆觸發重算。
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
    """取得本次排程處理目標長者 ID 清單。

    支援 payload 指定 elder_ids 做單獨重跑；預設 Scan elders 表，並受 SUMMARY_SWEEP_LIMIT 數量約束避免超時。
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

