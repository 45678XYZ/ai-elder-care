"""Cognito pre-token-generation trigger：發 ID token 前注入 elder_id claim。

長者身分不存在 Cognito，而是查 auth 維護的 sub→elder_id 對應表（見
terraform/dynamodb.tf 的 elder_accounts）：
- 帳號在對應表中有 elder_id → 注入 elder_id claim（後端 auth 據此判定為長者）
- 查無（照護者帳號）→ 不注入，token 無 elder_id claim → 後端視為照護者

自成一包、不依賴 src.shared，打包單一檔即可（見 terraform/lambda.tf）。
"""
import os

import boto3

ELDER_ACCOUNTS_TABLE = os.environ.get("ELDER_ACCOUNTS_TABLE", "elder_accounts")

_table = None


def handler(event, context):
    sub = event["request"]["userAttributes"].get("sub")
    elder_id = _lookup_elder_id(sub) if sub else None
    if elder_id:
        event["response"]["claimsOverrideDetails"] = {
            "claimsToAddOrOverride": {"elder_id": elder_id}
        }
    return event


def _lookup_elder_id(sub: str):
    """查對應表；帳號有綁定回 elder_id，否則回 None。"""
    resp = _accounts_table().get_item(Key={"sub": sub})
    item = resp.get("Item")
    return item.get("elder_id") if item else None


def _accounts_table():
    """延遲建立 DynamoDB resource，避免 import 期即連線（利於測試與冷啟動）。"""
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(ELDER_ACCOUNTS_TABLE)
    return _table
