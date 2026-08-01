# ASR SageMaker inference contract

所有 ASR 模型使用 SageMaker real-time endpoint，並對 Lambda 暴露同一份契約。模型與 prompt
差異只存在 container／部署層；Lambda adapter 不分支處理模型框架。

## Health check

Container 的 `GET /ping` 必須回 HTTP 200。

## Invocation

`POST /invocations`：

| 項目 | 固定值 |
|---|---|
| `Content-Type` | `application/octet-stream` |
| `Accept` | `application/json` |
| Body | raw PCM signed 16-bit little-endian；16 kHz、mono、最長 60 秒／1,920,000 bytes |
| CustomAttributes | `language=<zh-TW|hak>;sample_rate_hz=16000;channels=1` |

Body 不是 WAV/M4A container 或 base64。不得傳 prompt ID、correlation／elder ID、token、
endpoint name 或其他 metadata。

## 成功回應

HTTP 200、`application/json`：

```json
{"text": "辨識結果"}
```

`text` 必須是 trim 後非空白的 Unicode string。Lambda 忽略其他欄位；缺少、null、非字串或
空白 `text` 皆為 `provider_invalid_response`。

## 錯誤與逾時

| 情況 | Lambda typed error |
|---|---|
| 429、throttling、model not ready、service unavailable | `provider_unavailable` |
| 其他 SDK／container 失敗 | `provider_failure` |
| 非 JSON、body 無法讀取或無效 `text` | `provider_invalid_response` |
| connect/read timeout | `deadline_exceeded` |

SDK 不自行反覆重試；是否 fallback 由 router 決定。錯誤 body 不解析，也不得把原始 body、
例外或 traceback 放入 typed error／log。

## Formo prompt 邊界

六個客語腔調各有固定 endpoint/container 部署設定。Lambda 依 reserve turn 保存的 profile
腔調選 route，但 request 與 CustomAttributes 都不含 prompt ID。endpoint 啟用與模型核准
仍是兩個獨立 gate。

## Container 日誌

允許：audio byte count／duration、推論延遲、成功狀態、固定錯誤分類、模型 revision。

禁止：音訊／samples、逐字稿、token、個資、CustomAttributes 原文、完整 response、原始例外。

Lambda 實作：`backend/src/shared/asr/providers.py`；Terraform：
`terraform/asr_models.tf`。契約測試應放在自動測試，不在本文件維護可執行 fixture。
