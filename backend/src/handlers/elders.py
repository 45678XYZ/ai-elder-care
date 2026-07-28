"""長者資料 API Lambda Handler (GET/POST/PATCH /elders)。

規格出處：docs/api.md 專章「長者資料」
"""

import json
from typing import Any, Dict

from src.shared import auth, db, responses


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """解析 Request Body 的 JSON。"""
    body = event.get("body")
    if not body:
        return {}
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except Exception:
        raise ValueError("INVALID_JSON")


def get_http_method(event: Dict[str, Any]) -> str:
    """提取 HTTP 請求方法。"""
    method = event.get("httpMethod")
    if not method and "requestContext" in event:
        method = event["requestContext"].get("http", {}).get("method")
    return (method or "GET").upper()


def get_path_elder_id(event: Dict[str, Any]) -> str | None:
    """從 pathParameters 提取 elder_id。"""
    path_params = event.get("pathParameters") or {}
    return path_params.get("elder_id") or path_params.get("id")


def handle_get_elders(event: Dict[str, Any]) -> Dict[str, Any]:
    """GET /elders 或 GET /elders/{elder_id}"""
    target_elder_id = get_path_elder_id(event)

    # 1. 取得登入呼叫者資訊
    try:
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    # 2. 如果指定了單筆 elder_id：GET /elders/{elder_id}
    if target_elder_id:
        try:
            auth.assert_can_access_elder(event, target_elder_id)
        except auth.AuthError as auth_err:
            return auth_err.response

        elder = db.get_elder(target_elder_id)
        if not elder:
            return responses.error(404, "ELDER_NOT_FOUND", "找不到該位長者資料")
        return responses.json_response(200, elder)

    # 3. 列表查詢：GET /elders
    if caller.role == auth.ROLE_ELDER:
        # 長者帳號：僅能取得對應自己的那一筆
        elder = db.get_elder(caller.elder_id)
        items = [elder] if elder else []
        return responses.json_response(200, {"items": items})

    elif caller.role == auth.ROLE_CAREGIVER:
        # 照護者帳號：取得 caregiver_ids 包含該 caregiver sub 的長者列表
        items = db.list_elders(caregiver_id=caller.user_id)
        return responses.json_response(200, {"items": items})

    return responses.error(403, "FORBIDDEN", "權限不足")


def handle_post_elder(event: Dict[str, Any]) -> Dict[str, Any]:
    """POST /elders — 建立長者資料 (僅限照護者)"""
    try:
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者帳號可建立長者資料")

    try:
        body = parse_body(event)
    except ValueError:
        return responses.error(400, "INVALID_JSON", "請求內文不是有效的 JSON 格式")

    # 防護：禁止帶入由 Server 託管的唯讀欄位
    server_owned_fields = ("elder_id", "caregiver_ids", "created_at", "updated_at")
    for f in server_owned_fields:
        if f in body:
            return responses.error(400, "INVALID_PARAMETER", f"不得直接提供系統託管欄位 {f}")

    name = body.get("name")
    if not name:
        return responses.error(400, "INVALID_PARAMETER", "缺少必填欄位 name")

    gender = body.get("gender")
    if gender and gender not in ("male", "female", "other"):
        return responses.error(400, "INVALID_PARAMETER", "gender 必須為 male, female 或 other")

    lang_pref = body.get("lang_preference", "zh-TW")
    if lang_pref not in ("zh-TW", "hak"):
        return responses.error(400, "INVALID_PARAMETER", "lang_preference 必須為 zh-TW 或 hak")

    elder_data = dict(body)
    elder_data["lang_preference"] = lang_pref
    # 自動將建立者的 sub 綁定至 caregiver_ids
    elder_data["caregiver_ids"] = [caller.user_id]

    try:
        created_elder = db.create_elder(elder_data)
        return responses.json_response(201, created_elder)
    except Exception as e:
        return responses.error(500, "INTERNAL_ERROR", f"建立長者資料失敗: {str(e)}")


def handle_patch_elder(event: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH /elders/{elder_id} — 部分更新長者資料 (僅限照護者)"""
    target_elder_id = get_path_elder_id(event)
    if not target_elder_id:
        return responses.error(400, "INVALID_PARAMETER", "未指定要更新的 elder_id")

    try:
        auth.assert_can_access_elder(event, target_elder_id)
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者帳號可修改長者資料")

    try:
        body = parse_body(event)
    except ValueError:
        return responses.error(400, "INVALID_JSON", "請求內文不是有效的 JSON 格式")

    # 防護：禁止傳入唯讀欄位
    server_owned_fields = ("elder_id", "caregiver_ids", "created_at", "updated_at")
    for f in server_owned_fields:
        if f in body:
            return responses.error(400, "INVALID_PARAMETER", f"不得修改系統託管欄位 {f}")

    gender = body.get("gender")
    if gender and gender not in ("male", "female", "other"):
        return responses.error(400, "INVALID_PARAMETER", "gender 必須為 male, female 或 other")

    lang_pref = body.get("lang_preference")
    if lang_pref and lang_pref not in ("zh-TW", "hak"):
        return responses.error(400, "INVALID_PARAMETER", "lang_preference 必須為 zh-TW 或 hak")

    try:
        updated_elder = db.update_elder(target_elder_id, body)
        return responses.json_response(200, updated_elder)
    except Exception as e:
        return responses.error(500, "INTERNAL_ERROR", f"更新長者資料失敗: {str(e)}")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """GET/POST/PATCH /elders 長者資料 Lambda 主進入點。"""
    try:
        method = get_http_method(event)

        if method == "GET":
            return handle_get_elders(event)
        elif method == "POST":
            return handle_post_elder(event)
        elif method == "PATCH":
            return handle_patch_elder(event)
        else:
            return responses.error(405, "METHOD_NOT_ALLOWED", f"不支援的 HTTP 方法: {method}")
    except Exception as e:
        print(f"[Error] elders handler unhandled exception: {e}")
        return responses.error(500, "INTERNAL_ERROR", f"內部系統錯誤: {str(e)}")
