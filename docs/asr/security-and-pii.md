# ASR 安全與 PII 邊界

通用政策見 [`docs/pii.md`](../pii.md)；本文件只定義 ASR 特有的音訊、逐字稿與遙測規則。

## 資料生命週期

```text
App base64 → Chat Lambda decode → CanonicalAudio
                                      ├─ memory stream → Amazon Transcribe → Transcript
                                      └─ raw PCM → SageMaker → Transcript
                         └────────── 全程 memory only ──────────┘
```

ASR 輸入不得寫入 Lambda `/tmp`、S3 或 DynamoDB。Amazon Transcribe 只使用 streaming API，
不得建立 batch transcription job 或輸出 bucket。送往 SageMaker 的 body 是 raw PCM，只附
`language`、`sample_rate_hz`、`channels`；不得附 prompt、generation language、correlation、
elder ID 或 token。

## 競賽資料限制

- AWS 競賽帳號只可使用合成音訊、模擬 persona 與非真實健康內容。
- 不得匯入真實長者聲音、逐字稿、姓名、聯絡資料、健康紀錄或可回推個人的素材。
- Staging/runtime evidence 只能保存去識別化 fixture ID、聚合品質／延遲／容量指標與固定
  failure category；不得以「模型驗證」為由保存音訊或完整逐字稿。

## 絕對不可記錄

- 原始／base64／PCM 音訊、partial/final transcript 或 provider `text`。
- elder ID、姓名、聯絡／健康資訊、HF token 或 AWS credentials。
- endpoint 名稱／Region、Formo prompt ID、generation language、AWS request ID 或原始
  provider response/event。
- 原始 SDK 例外、traceback 或可能含敏感內容的自由文字。

Lambda、Transcribe adapter 與 container 都只可記錄固定分類、byte count／duration、延遲、
成功狀態與模型 revision。typed error 與對外 5xx 必須使用固定安全訊息。

## Lambda 遙測 allowlist

每次 `AsrFacade.recognize` 恰好產生一筆紀錄，鍵只能是：

```text
correlation_id, language, route, provider_id, input_format,
canonical_sample_rate_hz, canonical_channels, audio_duration_ms,
deadline_outcome, terminal_outcome, error_category, elapsed_ms, retryable,
attempt_count, failover_occurred
```

- 值只可為 UUID、enum、boolean 或聚合數值，不得為自由文字。
- `provider_id` 是設定中的非敏感識別名；fallback 成功時記實際服務者。
- 新增或移除欄位時同步 `TELEMETRY_ALLOWLIST_KEYS`、本清單與測試。

SageMaker container 亦不得記錄 CustomAttributes 原文、逐字稿、audio samples、原始回應或
原始例外；Transcribe adapter 不得記錄 transcript events 或 SDK metadata。
