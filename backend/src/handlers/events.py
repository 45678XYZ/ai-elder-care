"""GET /events — 生活事件時間軸 API。規格見 docs/api.md。

處理流程：
1. 驗證權限：確認請求者具備指定 elder_id 的讀取權限
2. 參數校驗：校驗 from / to 日期格式（YYYY-MM-DD）、type 分類與 limit 分頁範圍
3. 資料查詢：從 DynamoDB events 表依條件倒序查詢事件
4. 資料投影：使用 EventResponse Pydantic 模型過濾洗滌，隱藏後端內部萃取細節
"""

from datetime import datetime
import logging
import re
from typing import Any, Dict, get_args

from src.extraction.temporal import day_key
from src.shared import auth, db, responses
from src.shared.models import EventResponse, EventType

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _serialize_event(item: Dict[str, Any]) -> Dict[str, Any]:
    """將資料層 event 實體經由 EventResponse 模型投影洗滌為符合 api.md 規範之公開字典。

    選填欄位若為 None（如手動事件之 conversation_id 或非 routine 事件之 routine_id）
    經由 exclude_none=True 自動過濾，不殘留 null 鍵。
    """
    return EventResponse.model_validate(item).model_dump(exclude_none=True)


def handler(event, context):
    """GET /events 入口；解析查詢參數、校驗權限並回傳公開事件時間軸。"""
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
        # next_token 由後端編碼，前端原樣帶回；解碼失敗代表遭改動或跨版本失效，應歸類為用戶端請求錯誤
        if "next_token" in str(exc):
            return responses.error(400, "INVALID_PARAMETER", "無效的 next_token")
        logger.exception("查詢事件時間軸失敗：elder_id=%s", elder_id)
        return responses.error(500, "INTERNAL_ERROR", "查詢事件失敗")

    body: Dict[str, Any] = {"items": [_serialize_event(item) for item in items]}
    if next_token:
        body["next_token"] = next_token
    return responses.json_response(200, body)


