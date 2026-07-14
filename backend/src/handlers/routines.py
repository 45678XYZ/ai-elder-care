"""例行公事 API。規格見 docs/api.md。

- GET  /routines?elder_id=                 定義列表（App 據此排本地通知）
- GET  /routines?elder_id=&date=           當日行程視圖（missed 查詢時動態判定）
- POST /routines                           建立（201 回完整物件）
- PATCH /routines/{routine_id}             部分更新／停用
- POST /routines/{routine_id}/complete     手動確認完成（兩端皆可；寫入 manual event；
                                           無排程回 400 ROUTINE_NOT_SCHEDULED；已完成冪等）
"""
from src.shared import responses


def handler(event, context):
    # TODO: 依 httpMethod / path 分派
    return responses.not_implemented()
