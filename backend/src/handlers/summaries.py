"""GET /summaries & POST /summaries/generate — 每日摘要 API Handler。規格見 docs/api.md。

處理途徑與流程：
1. GET /summaries（列表查詢）：
   - 權限驗證：確認請求者具備指定 elder_id 之存取權限
   - 範圍計算：支援 from/to 區間查詢（預設最近 7 天，依台灣日界），倒序分頁回傳
   - 安全過濾：僅回傳已生成摘要之日期與公開白名單欄位

2. POST /summaries/generate（同步手動生成）：
   - 權限驗證：確認請求者具備指定 elder_id 之存取權限
   - 同步觸發：呼叫 shared/summarizer 共用邏輯，無縫相容 EventBridge 排程生成機制
   - 覆寫優先序控制：若既有摘要具較高完整度（如 complete），新產生的 partial 摘要不會覆寫舊摘要，此時回傳既有版本
"""

from datetime import datetime, timedelta
from typing import Any
import json
import logging
import re

from src.extraction.temporal import day_key, parse_ts
from src.shared import auth, bedrock, db, responses, summarizer
from src.shared.models import SUMMARY_SECTION_KEYS, DailySummaryResponse

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

# 未指定 from/to 時的預設範圍（含首尾共 7 天）
DEFAULT_RANGE_DAYS = 7

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def handler(event, context):
    """每日摘要 Lambda 入口；依 HTTP Method 分派至對應處置函式。"""
    method = (event.get("httpMethod") or "").upper()
    if method == "GET":
        return handle_list(event)
    if method == "POST":
        return handle_generate(event)
    return responses.error(400, "INVALID_PARAMETER", "不支援的請求方法")


# -----------------------------------------------------------------------------
# GET /summaries
# -----------------------------------------------------------------------------


def handle_list(event) -> dict[str, Any]:
    """處理 GET /summaries 請求，查詢指定日期區間之每日摘要時間軸。"""
    params = event.get("queryStringParameters") or {}

    elder_id = (params.get("elder_id") or "").strip()
    if not elder_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 elder_id")

    try:
        auth.assert_can_access_elder(event, elder_id)
    except auth.AuthError as exc:
        return exc.response

    today = day_key(datetime.now(db.TZ_TAIPEI))
    to_date = (params.get("to") or today).strip()
    from_date = (params.get("from") or "").strip()
    if not from_date:
        # 未指定 from 時，以 to_date 為基準往前回推 7 天（含首尾）
        if not _DATE_PATTERN.match(to_date):
            return responses.error(400, "INVALID_PARAMETER", "日期格式須為 YYYY-MM-DD")
        from_date = _shift_days(to_date, -(DEFAULT_RANGE_DAYS - 1))

    for value in (from_date, to_date):
        if not _DATE_PATTERN.match(value):
            return responses.error(400, "INVALID_PARAMETER", "日期格式須為 YYYY-MM-DD")
    if from_date > to_date:
        return responses.error(400, "INVALID_PARAMETER", "from 不得晚於 to")

    raw_limit = (params.get("limit") or "").strip()
    limit = DEFAULT_LIMIT
    if raw_limit:
        if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= MAX_LIMIT:
            return responses.error(400, "INVALID_PARAMETER", f"limit 須為 1–{MAX_LIMIT}")
        limit = int(raw_limit)

    try:
        items, next_token = db.list_daily_summaries(
            elder_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            next_token=(params.get("next_token") or "").strip() or None,
        )
    except db.DBError as exc:
        # next_token 由後端編碼，前端原樣帶回；解碼失敗代表遭改動或跨版本失效，應歸類為用戶端請求錯誤
        if "next_token" in str(exc):
            return responses.error(400, "INVALID_PARAMETER", "無效的 next_token")
        logger.exception("查詢每日摘要失敗：elder_id=%s", elder_id)
        return responses.error(500, "INTERNAL_ERROR", "查詢摘要失敗")

    body: dict[str, Any] = {"items": [to_public_summary(item) for item in items]}
    if next_token:
        body["next_token"] = next_token
    return responses.json_response(200, body)


# -----------------------------------------------------------------------------
# POST /summaries/generate
# -----------------------------------------------------------------------------


def handle_generate(event) -> dict[str, Any]:
    """處理 POST /summaries/generate 請求，同步觸發生成並儲存指定日期之摘要。"""
    try:
        payload = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        return responses.error(400, "INVALID_PARAMETER", "request body 須為 JSON")
    if not isinstance(payload, dict):
        return responses.error(400, "INVALID_PARAMETER", "request body 須為 JSON 物件")

    elder_id = str(payload.get("elder_id") or "").strip()
    if not elder_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 elder_id")

    try:
        auth.assert_can_access_elder(event, elder_id)
    except auth.AuthError as exc:
        return exc.response

    date = str(payload.get("date") or day_key(datetime.now(db.TZ_TAIPEI))).strip()
    if not _DATE_PATTERN.match(date):
        return responses.error(400, "INVALID_PARAMETER", "日期格式須為 YYYY-MM-DD")

    try:
        # 手動生成不等待排程窗口，可合法產出 data_status=partial；written=False 代表既有摘要具較高完整度（覆寫優先序擋下本次），回傳既有版本仍屬成功
        summary, written = summarizer.generate_and_store(elder_id, date)
    except bedrock.BedrockError:
        logger.exception("摘要生成的模型呼叫失敗：elder_id=%s date=%s", elder_id, date)
        return responses.error(500, "INTERNAL_ERROR", "摘要生成失敗")
    except db.DBError:
        logger.exception("摘要寫入失敗：elder_id=%s date=%s", elder_id, date)
        return responses.error(500, "INTERNAL_ERROR", "摘要生成失敗")

    if not written:
        logger.info("既有摘要優先，回傳既有版本：elder_id=%s date=%s", elder_id, date)
    return responses.json_response(200, to_public_summary(summary))


# -----------------------------------------------------------------------------
# 投影
# -----------------------------------------------------------------------------


def to_public_summary(item: dict[str, Any]) -> dict[str, Any]:
    """將資料層 daily_summaries 實體投影為符合 api.md 規範之公開字典。

    透過 DailySummaryResponse 模型洗滌：隱藏 input_through_at、completeness_rank 等
    內部欄位，並保證 sections 七個鍵完整存在（無資料補 null）、其餘欄位提供預設值，
    避免前端拿到 undefined 造成渲染錯誤。
    """
    # 補齊 sections 七鍵與預設值，讓 DailySummaryResponse 的必填欄位能通過驗證
    normalized = dict(item)
    sections = normalized.get("sections") or {}
    normalized["sections"] = {
        key: sections.get(key) if sections.get(key) else None for key in SUMMARY_SECTION_KEYS
    }
    normalized.setdefault("alerts", [])
    normalized.setdefault("routines", {"completed": 0, "missed": 0, "items": []})
    normalized.setdefault("interaction_count", 0)
    normalized.setdefault("pending_session_count", 0)
    normalized.setdefault("overview", "")
    normalized.setdefault("data_status", "complete")
    normalized.setdefault("generated_at", "")

    return DailySummaryResponse.model_validate(normalized).model_dump(exclude_none=True)


def _shift_days(date: str, days: int) -> str:
    """計算相對於指定日期的位移天數日期字串（YYYY-MM-DD）。"""
    return day_key(parse_ts(f"{date}T00:00:00+08:00") + timedelta(days=days))

