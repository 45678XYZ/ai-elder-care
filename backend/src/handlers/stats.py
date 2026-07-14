"""統計 API。規格見 docs/api.md。

- GET /stats?elder_id=&days=7   today／period／routines 逐項完成／daily 趨勢
  interaction_count 一律指 /chat 對話輪數
"""
from src.shared import responses


def handler(event, context):
    # TODO: 實作
    return responses.not_implemented()
