"""長者資料 API Lambda Handler (GET/POST/PATCH /elders)。

規格出處：docs/api.md 專章「長者資料」
"""

import json
from typing import Any, Dict

from pydantic import ValidationError

from src.shared import auth, db, responses
from src.shared.models import ElderCreate, ElderResponse, ElderUpdate
from src.shared.validation import RequestValidationError, validate


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
        raise RequestValidationError(responses.error(400, "INVALID_JSON", "請求內文不是有效的 JSON 格式"))



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


def get_path_note_id(event: Dict[str, Any]) -> str | None:
    """從 pathParameters 提取 health note 的 note_id。"""
    path_params = event.get("pathParameters") or {}
    return path_params.get("note_id")


def is_health_notes_path(event: Dict[str, Any]) -> bool:
    """判斷這個請求打的是 /elders/{elder_id}/health_notes 子資源。"""
    path = event.get("path") or event.get("rawPath") or ""
    if "health_notes" in path:
        return True
    # 部分測試與 REST API 事件不帶完整 path，改看 pathParameters 是否出現 note_id
    return get_path_note_id(event) is not None


def _serialize_elder(elder_dict: Dict[str, Any]) -> Dict[str, Any]:
    """使用 ElderResponse 過濾洗滌要回傳給前端的長者資料。"""
    return ElderResponse.model_validate(elder_dict).model_dump(exclude_none=True)


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
        return responses.json_response(200, _serialize_elder(elder))

    # 3. 列表查詢：GET /elders
    if caller.role == auth.ROLE_ELDER:
        # 長者帳號：僅能取得對應自己的那一筆
        elder = db.get_elder(caller.elder_id)
        items = [_serialize_elder(elder)] if elder else []
        return responses.json_response(200, {"items": items})

    elif caller.role == auth.ROLE_CAREGIVER:
        # 照護者帳號：取得 caregiver_ids 包含該 caregiver sub 的長者列表
        raw_items = db.list_elders(caregiver_id=caller.user_id)
        items = [_serialize_elder(item) for item in raw_items]
        return responses.json_response(200, {"items": items})

    return responses.error(403, "FORBIDDEN", "權限不足")


def handle_post_elder(event: Dict[str, Any]) -> Dict[str, Any]:
    """POST /elders — 建立長者資料 (照護者或長者自註冊)

    長者首次設定時 token 尚無 elder_id claim（elder_accounts 表為空），後端視為照護者，
    因此 role check 通過。帶 self_register=true 時同時寫入 elder_accounts 表，下次
    登入 pre-token-generation trigger 即可注入 elder_id claim。
    """
    try:
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者帳號可建立長者資料")

    body = parse_body(event)

    self_register = body.pop("self_register", False)

    # 防護：禁止帶入由 Server 託管的唯讀欄位
    server_owned_fields = ("elder_id", "caregiver_ids", "created_at", "updated_at")
    for f in server_owned_fields:
        if f in body:
            return responses.error(400, "INVALID_PARAMETER", f"不得直接提供系統託管欄位 {f}")

    # 使用 Pydantic 進行完整請求驗證
    payload = validate(ElderCreate, body)
    elder_data = payload.model_dump(exclude_none=True)
    # 自動將建立者的 sub 綁定至 caregiver_ids
    elder_data["caregiver_ids"] = [caller.user_id]

    try:
        created_elder = db.create_elder(elder_data)

        if self_register:
            db.bind_elder_account(caller.user_id, created_elder["elder_id"])

        return responses.json_response(201, _serialize_elder(created_elder))
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

    body = parse_body(event)

    # 長者本人只能修改語言相關欄位
    _ELDER_ALLOWED_FIELDS = {"lang_preference", "hakka_dialect"}
    if caller.role != auth.ROLE_CAREGIVER:
        if not set(body.keys()).issubset(_ELDER_ALLOWED_FIELDS):
            return responses.error(403, "FORBIDDEN", "長者只能修改語言偏好與腔調")

    # 防護：禁止傳入唯讀欄位
    server_owned_fields = ("elder_id", "caregiver_ids", "created_at", "updated_at")
    for f in server_owned_fields:
        if f in body:
            return responses.error(400, "INVALID_PARAMETER", f"不得修改系統託管欄位 {f}")

    # 使用 Pydantic 驗證部分更新欄位
    payload = validate(ElderUpdate, body)
    patch_data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not patch_data:
        return responses.error(400, "INVALID_PARAMETER", "未提供可更新的欄位")

    try:
        updated_elder = db.update_elder(target_elder_id, patch_data)
        return responses.json_response(200, _serialize_elder(updated_elder))
    except Exception as e:
        return responses.error(500, "INTERNAL_ERROR", f"更新長者資料失敗: {str(e)}")


def handle_post_health_note(event: Dict[str, Any]) -> Dict[str, Any]:
    """POST /elders/{elder_id}/health_notes — 新增單筆健康註記 (僅限照護者)

    與 `PATCH /elders` 送整份 health_notes 的差別在於這裡是原子 append：
    照護者新增的同時 AI 也可能在對話中補一筆，整份覆寫會讓其中一邊消失。
    """
    target_elder_id = get_path_elder_id(event)
    if not target_elder_id:
        return responses.error(400, "INVALID_PARAMETER", "未指定 elder_id")

    try:
        auth.assert_can_access_elder(event, target_elder_id)
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者帳號可修改長者資料")

    body = parse_body(event)
    text = (body.get("text") or "").strip()
    if not text:
        return responses.error(400, "INVALID_PARAMETER", "health note 的 text 不得為空")

    # source 不接受 client 指定：這個端點只給照護者用，AI 補的那條路走 update_elder_profile
    # 工具。開放 client 自稱 agent 的話，來源標示就失去它存在的意義。
    if "source" in body:
        return responses.error(400, "INVALID_PARAMETER", "不得直接指定 source")

    try:
        updated = db.append_health_note(target_elder_id, {"text": text, "source": "caregiver"})
        return responses.json_response(201, _serialize_elder(updated))
    except Exception as e:
        return responses.error(500, "INTERNAL_ERROR", f"新增健康註記失敗: {str(e)}")


def handle_delete_health_note(event: Dict[str, Any]) -> Dict[str, Any]:
    """DELETE /elders/{elder_id}/health_notes/{note_id} — 刪除單筆健康註記 (僅限照護者)"""
    target_elder_id = get_path_elder_id(event)
    note_id = get_path_note_id(event)
    if not target_elder_id or not note_id:
        return responses.error(400, "INVALID_PARAMETER", "未指定 elder_id 或 note_id")

    try:
        auth.assert_can_access_elder(event, target_elder_id)
        caller = auth.get_caller(event)
    except auth.AuthError as auth_err:
        return auth_err.response

    if caller.role != auth.ROLE_CAREGIVER:
        return responses.error(403, "FORBIDDEN", "只有照護者帳號可修改長者資料")

    try:
        updated = db.remove_health_note(target_elder_id, note_id)
    except Exception as e:
        return responses.error(500, "INTERNAL_ERROR", f"刪除健康註記失敗: {str(e)}")

    if updated is None:
        return responses.error(404, "HEALTH_NOTE_NOT_FOUND", "找不到該筆健康註記")
    return responses.json_response(200, _serialize_elder(updated))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """GET/POST/PATCH /elders 長者資料 Lambda 主進入點。"""
    try:
        method = get_http_method(event)

        if is_health_notes_path(event):
            if method == "POST":
                return handle_post_health_note(event)
            elif method == "DELETE":
                return handle_delete_health_note(event)
            return responses.error(405, "METHOD_NOT_ALLOWED", f"不支援的 HTTP 方法: {method}")

        if method == "GET":
            return handle_get_elders(event)
        elif method == "POST":
            return handle_post_elder(event)
        elif method == "PATCH":
            return handle_patch_elder(event)
        else:
            return responses.error(405, "METHOD_NOT_ALLOWED", f"不支援的 HTTP 方法: {method}")
    except (auth.AuthError, RequestValidationError) as exc:
        return exc.response
    except Exception as e:
        print(f"[Error] elders handler unhandled exception: {e}")
        return responses.error(500, "INTERNAL_ERROR", f"內部系統錯誤: {str(e)}")


