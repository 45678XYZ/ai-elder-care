"""生活記錄（Module B）事件萃取 pipeline。

只由 batch 相關 Lambda 使用，不進 realtime `/chat` 路徑。
設計與移植步驟見 docs/feature_events-extraction.md，資料契約見 docs/framework.md。
"""
