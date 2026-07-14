"""每日摘要 API。規格見 docs/api.md。

- GET  /summaries?elder_id=&from=&to=   僅回已生成日期；sections 固定六類、無資料為 null
- POST /summaries/generate              手動觸發（同步、同日覆寫；無對話仍回 200）
"""
from src.shared import responses


def handler(event, context):
    # TODO: 依 httpMethod / path 分派；生成邏輯與 summary_generator 共用
    return responses.not_implemented()
