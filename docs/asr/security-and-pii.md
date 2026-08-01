# ASR 安全與 PII 邊界

通用政策見 [`docs/pii.md`](../pii.md)；本文件只定義 ASR 特有的音訊、逐字稿與遙測規則。

## 資料生命週期

```text
App base64 → Chat Lambda decode → CanonicalAudio → SageMaker → Transcript
                         └────────── 全程 memory only ──────────┘
```

ASR 輸入不得寫入 Lambda `/tmp`、S3 或 DynamoDB。送往 SageMaker 的 body 是 raw PCM，
只附 `language`、`sample_rate_hz`、`channels`；不得附 prompt、correlation、elder ID 或 token。

## 絕對不可記錄

- 原始／base64／PCM 音訊、逐字稿或 provider `text`。
- elder ID、姓名、聯絡／健康資訊、HF token 或 AWS credentials。
- endpoint 名稱／Region、Formo prompt ID、原始 provider response。
- 原始 SDK 例外、traceback 或可能含敏感內容的自由文字。

Lambda 與 container 都只可記錄固定分類、byte count／duration、延遲、成功狀態與模型 revision。
typed error 與對外 5xx 必須使用固定安全訊息。

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

Container 亦不得記錄 CustomAttributes 原文、逐字稿、audio samples、原始回應或原始例外。
