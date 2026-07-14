"""DynamoDB 存取層。

六張表（見框架文件「資料模型」）：
- elders            長者 persona
- conversations     對話紀錄
- events            結構化生活事件——「實際發生」的唯一紀錄（含例行公事完成）
- daily_summaries   AI 每日摘要
- memories          長期記憶
- routines          例行公事計畫與完成狀態（與 events 同一次對話處理中一併更新）
"""

# TODO: table resources 與各表的讀寫函式
