"""每日摘要 API。規格見 docs/api.md 的「每日摘要」。

- `GET  /summaries?elder_id=&from=&to=`   只回已生成日期，最新日期優先
- `POST /summaries/generate`              同步生成，可合法回 `data_status=partial`

回應只公開 api.md 列出的欄位：`input_through_at`、`completeness_rank`、`generator_version`
與 `schema_version` 是後端內部欄位（覆寫優先序用），一律不外流。

生成邏輯在 `shared/summarizer.py`，與排程 generator 共用同一份實作，避免手動與排程算出
不同結果。
"""

from datetime import datetime, timedelta
from typing import Any
import json
import logging
import re

from src.extraction.temporal import day_key, parse_ts
from src.shared import auth, bedrock, db, responses, summarizer
from src.shared.models import SUMMARY_SECTION_KEYS

logger = logging.getLogger(__name__)

# 白名單投影：資料層之後新增內部欄位時不會無聲外流
PUBLIC_SUMMARY_FIELDS: tuple[str, ...] = (
    "elder_id",
    "date",
    "overview",
    "sections",
    "routines",
    "alerts",
    "interaction_count",
    "data_status",
    "pending_session_count",
    "generated_at",
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

# 未指定 from/to 時的預設範圍（含首尾共 7 天）
DEFAULT_RANGE_DAYS = 7

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def handler(event, context):
    """依 HTTP method 分派：GET 列表、POST 手動生成。"""
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
        # 預設最近 7 天（含首尾），以 to 為基準往回推
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
        # next_token 由後端編碼，前端原樣帶回；解不開代表被改過或跨版本，屬請求問題
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
        # 手動生成不等待排程窗口，因此可能回 partial；written=False 代表既有摘要更新或
        # 更完整（覆寫優先序擋下本次），此時回既有那份，對呼叫端仍是成功。
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
    """投影成 api.md 的摘要物件。

    `sections` 七個 key 必須完整存在（無資料為 null），因此缺 key 一律補齊；其餘欄位缺值
    才給契約預設值，避免前端拿到 `undefined`。
    """
    projected = {field: item.get(field) for field in PUBLIC_SUMMARY_FIELDS}
    sections = projected.get("sections") or {}
    projected["sections"] = {
        key: sections.get(key) if sections.get(key) else None for key in SUMMARY_SECTION_KEYS
    }
    projected["alerts"] = projected.get("alerts") or []
    projected["routines"] = projected.get("routines") or {"completed": 0, "missed": 0, "items": []}
    projected["interaction_count"] = projected.get("interaction_count") or 0
    projected["pending_session_count"] = projected.get("pending_session_count") or 0
    return projected


def _shift_days(date: str, days: int) -> str:
    return day_key(parse_ts(f"{date}T00:00:00+08:00") + timedelta(days=days))
