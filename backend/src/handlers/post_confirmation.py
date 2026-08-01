"""Cognito Post Confirmation Trigger.

當使用者完成 Cognito 註冊與 Email 驗證後，AWS Cognito 會自動呼叫此 Lambda。
目前僅做日誌記錄；照護者通知改由 tools Lambda 的 notify_caregiver 透過 SES
精確寄送給綁定該長者的照護者，不再使用全域 SNS Topic 廣播。
"""
import logging
from typing import Any, Dict

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Cognito Trigger 進入點"""
    logger.info(f"PostConfirmation event: triggerSource={event.get('triggerSource')}")
    return event
