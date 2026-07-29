"""統一回應格式（docs/api.md 共通慣例）。"""
import json


def json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def error(status_code: int, code: str, message: str) -> dict:
    """錯誤格式：{ "error": { "code", "message" } }。code 為穩定識別碼，message 僅供人讀。"""
    return json_response(status_code, {"error": {"code": code, "message": message}})


def not_implemented() -> dict:
    return error(501, "NOT_IMPLEMENTED", "尚未實作")
