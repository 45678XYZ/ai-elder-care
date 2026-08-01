# ASR 遠端 provider inference contract

Lambda 的 canonical audio 一律為 mono、16 kHz、PCM S16LE，並依 provider adapter 送往
Amazon Transcribe Streaming 或 SageMaker real-time endpoint。兩條路徑都必須保留同一組
deadline、cancellation、typed error 與 PII-safe logging 邊界。

## Amazon Transcribe Streaming

固定 provider ID 為 `amazon_transcribe_zh_tw`。Provider 內部鎖定：

| 項目 | 固定值 |
|---|---|
| API | `StartStreamTranscription` |
| LanguageCode | `zh-TW` |
| MediaEncoding | `pcm` |
| MediaSampleRateHertz | `16000` |
| Input | 依序傳送 canonical PCM chunks，完成後關閉 input stream |

- 不接受 `ASR_CONFIG_JSON` 覆寫 language code、encoding、sample rate 或 service 名稱。
- 忽略 partial results；依事件順序合併 final results，trim 後必須為非空白 Unicode string。
- 不使用 batch transcription、S3 input/output 或持久化暫存。
- async event stream 必須受呼叫端 deadline 與 cancellation 約束；結束、取消或失敗時關閉
  input stream，不留下背景工作。

錯誤映射：

| 情況 | Lambda typed error |
|---|---|
| throttling、service unavailable、連線暫不可用 | `provider_unavailable` |
| SDK／event stream 失敗 | `provider_failure` |
| stream 結束但沒有非空 final transcript、event shape 無效 | `provider_invalid_response` |
| deadline 到期 | `deadline_exceeded` |
| 呼叫端取消 | `cancelled` |

只有前三種 provider 錯誤允許 router 嘗試 CE；deadline 與 cancellation 不 fallback。不得把原始
SDK 例外、event payload、逐字稿或 request ID 放入 typed error／log。

## SageMaker real-time endpoint

CE 與六個 Formo endpoints 對 Lambda 暴露同一份契約。模型、prompt 與 generation language
差異只存在 container／部署層；Lambda adapter 不依模型框架分支。

### Health check

Container 的 `GET /ping` 必須回 HTTP 200。

### Invocation

`POST /invocations`：

| 項目 | 固定值 |
|---|---|
| `Content-Type` | `application/octet-stream` |
| `Accept` | `application/json` |
| Body | raw PCM signed 16-bit little-endian；16 kHz、mono、最長 60 秒／1,920,000 bytes |
| CustomAttributes | `language=<zh-TW|hak>;sample_rate_hz=16000;channels=1` |

Body 不是 WAV/M4A container 或 base64。不得傳 prompt ID、generation language、correlation／
elder ID、token、endpoint name 或其他 metadata。

### 成功回應

HTTP 200、`application/json`：

```json
{"text": "辨識結果"}
```

`text` 必須是 trim 後非空白的 Unicode string。Lambda 忽略其他欄位；缺少、null、非字串或
空白 `text` 皆為 `provider_invalid_response`。

### 錯誤與逾時

| 情況 | Lambda typed error |
|---|---|
| 429、throttling、model not ready、service unavailable | `provider_unavailable` |
| 其他 SDK／container 失敗 | `provider_failure` |
| 非 JSON、body 無法讀取或無效 `text` | `provider_invalid_response` |
| connect/read timeout | `deadline_exceeded` |

SDK 不自行反覆重試；是否 fallback 由 router 決定。錯誤 body 不解析，也不得把原始 body、
例外或 traceback 放入 typed error／log。

## Formo 部署邊界

六個客語腔調各有固定 endpoint/container 部署設定：

- `FORMO_PROMPT_ID` 固定為該 endpoint 的腔調 wire value。
- `FORMO_GENERATION_LANGUAGE=Chinese` 固定輸出客語漢字；它不是 `zh-TW` capability。
- Lambda 依 reserve turn 保存的 profile 腔調選 route，但 request 與 CustomAttributes 都不含
  prompt ID 或 generation language。

Endpoint 啟用與模型核准仍是兩個獨立 gate。

## Provider 日誌

允許：audio byte count／duration、推論延遲、成功狀態、固定錯誤分類、模型 revision。

禁止：音訊／samples、partial 或 final transcript、token、個資、CustomAttributes 原文、完整
response／event、endpoint／Region、AWS request ID 或原始例外。

Lambda 實作：`backend/src/shared/asr/providers.py`；Terraform：
`terraform/asr_models.tf`。契約測試應放在自動測試，不在本文件維護可執行 fixture。
