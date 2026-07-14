"""每日摘要生成（EventBridge Scheduler 每晚觸發）。

讀取當日 conversations／events／routines 完成快照，
參考近幾日紀錄以標記跨日警訊（alerts），寫入 daily_summaries。
與 POST /summaries/generate 共用生成邏輯。
"""


def handler(event, context):
    # TODO: 實作
    raise NotImplementedError
