"""Cognito Post Confirmation Trigger.

當照護者完成 Cognito 註冊與 Email 驗證後，AWS Cognito 會自動呼叫此 Lambda。
此 Lambda 負責萃取使用者的 Email，並自動將其訂閱至指定的 SNS Topic，
讓使用者能接收「緊急警報」與「晚報」。使用者只需至信箱點擊確認信即可開通。
"""
import os
import boto3
import logging
from typing import Any, Dict

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CAREGIVER_NOTIFY_TOPIC_ARN = os.environ.get("CAREGIVER_NOTIFY_TOPIC_ARN", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

_sns_client = None

def get_sns_client():
    """取得 SNS Client（Warm Start 重用）"""
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns", region_name=AWS_REGION)
    return _sns_client


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Cognito Trigger 進入點"""
    logger.info(f"Received event: {event}")
    
    # 確保是正確的 Trigger 來源
    if event.get("triggerSource") != "PostConfirmation_ConfirmSignUp":
        logger.info("Not a ConfirmSignUp event. Skipping SNS subscription.")
        return event

    user_attributes = event.get("request", {}).get("userAttributes", {})
    email = user_attributes.get("email")

    if not email:
        logger.warning("No email found in userAttributes. Skipping SNS subscription.")
        return event

    if not CAREGIVER_NOTIFY_TOPIC_ARN:
        logger.info(f"[MOCK] Would subscribe {email} to SNS topic, but ARN is not set.")
        return event

    try:
        sns = get_sns_client()
        logger.info(f"Subscribing {email} to {CAREGIVER_NOTIFY_TOPIC_ARN}")
        sns.subscribe(
            TopicArn=CAREGIVER_NOTIFY_TOPIC_ARN,
            Protocol="email",
            Endpoint=email,
            ReturnSubscriptionArn=True
        )
        logger.info(f"Successfully sent subscription request to {email}.")
    except Exception as e:
        logger.error(f"Failed to subscribe {email} to SNS: {str(e)}")
        # 注意：不要 raise Exception，否則可能會阻礙 Cognito 流程
        # 紀錄錯誤即可，讓使用者的帳號能順利建立

    # Cognito triggers 必須將原本的 event 原封不動回傳，才能完成流程
    return event
