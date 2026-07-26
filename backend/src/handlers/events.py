"""生活事件 API。規格見 docs/api.md。

- GET /events?elder_id=&from=&to=&type=   事件時間軸；`type` 與摘要 sections 一一對應

回應只公開 api.md 列出的欄位。canonical key、extraction track、revision、chunk、
`concept_id`、`structured_detail` 與 evidence 清單都是後端內部資訊，一律不外流。
"""

from datetime import datetime
import logging
import re
from typing import Any, get_args

from src.extraction.temporal import day_key
from src.shared import auth, db, responses
from src.shared.models import EventType

logger = logging.getLogger(__name__)

# 回應允許出現的欄位；以白名單而非黑名單，避免資料層新增內部欄位時無聲外流
PUBLIC_EVENT_FIELDS: tuple[str, ...] = (
    "event_id",
    "elder_id",
    "ts",
    "type",
    "detail",
    "source",
    "conversation_id",
    "routine_id",
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def handler(event, context):
    params = event.get("queryStringParameters") or {}

    elder_id = (params.get("elder_id") or "").strip()
    if not elder_id:
        return responses.error(400, "INVALID_PARAMETER", "缺少 elder_id")

    try:
        auth.assert_can_access_elder(event, elder_id)
    except auth.AuthError as exc:
        return exc.response

    today = day_key(datetime.now(db.TZ_TAIPEI))
    from_date = (params.get("from") or today).strip()
    to_date = (params.get("to") or today).strip()
    for value in (from_date, to_date):
        if not _DATE_PATTERN.match(value):
            return responses.error(400, "INVALID_PARAMETER", "日期格式須為 YYYY-MM-DD")
    if from_date > to_date:
        return responses.error(400, "INVALID_PARAMETER", "from 不得晚於 to")

    event_type = (params.get("type") or "").strip() or None
    if event_type and event_type not in get_args(EventType):
        return responses.error(400, "INVALID_PARAMETER", "type 不在允許的分類內")

    raw_limit = (params.get("limit") or "").strip()
    limit = DEFAULT_LIMIT
    if raw_limit:
        if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= MAX_LIMIT:
            return responses.error(400, "INVALID_PARAMETER", f"limit 須為 1–{MAX_LIMIT}")
        limit = int(raw_limit)

    try:
        items, next_token = db.list_events(
            elder_id,
            from_date=from_date,
            to_date=to_date,
            event_type=event_type,
            limit=limit,
            next_token=(params.get("next_token") or "").strip() or None,
        )
    except db.DBError as exc:
        # next_token 由後端編碼，前端原樣帶回；解不開代表被改過或跨版本，屬請求問題
        if "next_token" in str(exc):
            return responses.error(400, "INVALID_PARAMETER", "無效的 next_token")
        logger.exception("查詢事件時間軸失敗：elder_id=%s", elder_id)
        return responses.error(500, "INTERNAL_ERROR", "查詢事件失敗")

    body: dict[str, Any] = {"items": [_to_public_event(item) for item in items]}
    if next_token:
        body["next_token"] = next_token
    return responses.json_response(200, body)


def _to_public_event(item: dict[str, Any]) -> dict[str, Any]:
    """投影成 api.md 的事件物件；缺值欄位省略而非回 null。"""
    return {
        field: item[field]
        for field in PUBLIC_EVENT_FIELDS
        if item.get(field) is not None
    }
