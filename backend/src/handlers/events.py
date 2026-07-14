"""生活事件 API。規格見 docs/api.md。

- GET /events?elder_id=&from=&to=&type=   事件時間軸；type 六類與摘要 sections 一一對應
"""
from src.shared import responses


def handler(event, context):
    # TODO: 實作（含 next_token 分頁）
    return responses.not_implemented()
