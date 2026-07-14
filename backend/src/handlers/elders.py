"""長者資料 API。規格見 docs/api.md。

- GET  /elders               列表（照護者：綁定的長者；長者：僅自己）
- GET  /elders/{elder_id}    單筆
- POST /elders               建立（201 回完整物件；建立者自動綁定 caregiver_ids）
- PATCH /elders/{elder_id}   部分更新（200 回完整物件）
"""
from src.shared import responses


def handler(event, context):
    # TODO: 依 httpMethod / path 分派
    return responses.not_implemented()
