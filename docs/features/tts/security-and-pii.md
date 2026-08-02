# TTS 安全與 PII 邊界

TTS 必須處理 Agent 回覆文字，因此資料最小化重點是「只送合成必要欄位、用完即丟」。

## 可送往 provider

- 合成文字、明確 `language`、客語 `dialect`、輸出格式。
- 由後端設定固定的 speaker；App 不可指定。

## 不可送出或記錄

- elder ID、姓名、caregiver ID、session／conversation ID、correlation ID。
- HF／AWS token、endpoint 名稱、原始 SDK 例外、provider 完整 response。
- 合成文字、音訊 bytes 或音訊內容摘要。
- voice-cloning reference audio、speaker embedding 或長者聲紋；本階段完全不蒐集。

Chat Lambda 日誌只允許 `correlation_id`、typed error category、成功／失敗與去識別化延遲；
provider ID 只可使用設定中的非敏感識別名。SageMaker container 只記模型 revision、延遲、
byte count 與固定錯誤分類。

合成 MP3 只存入專用 S3 bucket 的 `tts/<conversation_id>.mp3`，DynamoDB 只保存 object key；
API 動態簽發短效 URL，bucket lifecycle 依整體 PII 政策清除。TTS 失敗時不保存半成品。

客語模型為 CC BY-NC 4.0。`license_cleared` 是 production gate，不得因 endpoint 已建立而
自動通過；用途轉商業時必須關閉 route 或取得另行授權。
