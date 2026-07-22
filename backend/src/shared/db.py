"""DynamoDB 存取層。

六張表（見框架文件「資料模型」）：
- elders            長者 persona
- conversations     對話紀錄
- events            結構化生活事件——「實際發生」的唯一紀錄（含例行公事完成）
- daily_summaries   AI 每日摘要
- memories          長期記憶
- routines          例行公事計畫與完成狀態（與 events 同一次對話處理中一併更新）

表名由 Terraform 於 Lambda 環境變數注入；本機測試取預設值。
"""
import os

import boto3

ELDERS_TABLE = os.environ.get("ELDERS_TABLE", "elders")

_resource = None


def _table(name: str):
    """延遲建立 DynamoDB resource，避免 import 期即連線（利於測試與冷啟動）。"""
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource.Table(name)


def get_elder(elder_id: str):
    """讀取單筆長者；不存在回 None。授權的 caregiver_ids 綁定檢查與 /elders 端點共用。"""
    resp = _table(ELDERS_TABLE).get_item(Key={"elder_id": elder_id})
    return resp.get("Item")


# TODO: 其餘表的讀寫（conversations／events／daily_summaries／memories／routines）與 GSI 查詢
