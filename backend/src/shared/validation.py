"""shared/validation.py — API Handler 共享之 Pydantic Request 校驗與 Error 封裝工具。"""

from typing import Any, Dict, Type, TypeVar
from pydantic import BaseModel, ValidationError
from src.shared import responses

T = TypeVar("T", bound=BaseModel)


class RequestValidationError(Exception):
    """請求不合法 Exception；已封裝完成之 HTTP 400 INVALID_PARAMETER Response。"""

    def __init__(self, response: Dict[str, Any]):
        super().__init__(response.get("body", ""))
        self.response = response


def bad_request(message: str) -> RequestValidationError:
    """產生帶有 400 INVALID_PARAMETER 的 RequestValidationError。"""
    return RequestValidationError(responses.error(400, "INVALID_PARAMETER", message))


def format_validation_message(exc: ValidationError) -> str:
    """取第一個欄位錯誤資訊組裝可讀訊息。"""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "body"
    return f"{location}: {first['msg']}"


def validate(model: Type[T], body: Dict[str, Any]) -> T:
    """驗證 Request Body；合規時回傳 Pydantic Model 實例，不合規時拋出 RequestValidationError。"""
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise bad_request(format_validation_message(e))
