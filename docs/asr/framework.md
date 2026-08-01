# ASR 子系統框架

## 目標與邊界

`POST /chat` 的 audio 輸入由 Chat Lambda 解碼，再交給 ASR。ASR 只負責把音訊轉成
非空白逐字稿或 typed error；它不認識 HTTP、認證、session、資料庫或聊天流程。

ASR 採 remote-only：Lambda 正規化音訊並呼叫 SageMaker real-time endpoint，不下載、
載入或執行模型。`ASR_CONFIG_JSON` 是唯一 ASR 設定來源；設定不完整、路由停用或模型
未核准時，一律 `route_not_approved` 且不外呼。

```text
Flutter audio
    │
    ▼
Chat Lambda ── deadline／cancellation／profile dialect
    │
    ▼
AsrFacade ── canonical audio (PCM S16LE, mono, 16 kHz, ≤ 60 s)
    │
    ▼
AsrRouter ── enabled route → approved providers → same-language fallback
    │
    ├─ CE SageMaker endpoint
    ├─ Formo SageMaker endpoint（每一腔固定一個部署設定）
    └─ hak_mock（僅單元測試與明確本機開發）
    │
    ▼
Transcript | TypedAsrError
```

## 路由規則

- 中文 route key：`zh-TW`。
- 客語 production route key：`hak:<profile dialect>`；六腔 wire values 見
  [`config-schema.md`](config-schema.md)。generic `hak` 只保留給本機 mock 相容。
- Formo prompt 固定在對應腔調的 endpoint/container；Lambda request 不傳 prompt ID。
- 只有 `provider_unavailable`、`provider_failure`、`provider_invalid_response` 可嘗試下一個
  provider。取消、逾期、音訊、語言或核准錯誤立即終止。
- `default_config()` 只讓 `hak_mock` 可用。Terraform 無論 endpoint 是否啟用都會注入
  明確 production 設定，因此部署環境不會意外使用 mock。

## 責任切分

| 元件 | 責任 |
|---|---|
| Flutter | 錄音、base64 編碼、送出 API；不可指定 provider |
| Chat handler | 解碼、取得 profile 腔調、設定 deadline、對映公開錯誤 |
| ASR facade/audio | 輸入門檻、正規化、終態遙測 |
| ASR router/providers | 設定驅動路由、核准 gate、fallback、SageMaker adapter |
| SageMaker container | 固定模型與 prompt、推論、回傳 `{ "text": "..." }` |

## 基礎設施

`terraform/asr_models.tf` 以 `asr_enable_endpoints` 控制一個 CE endpoint 與六個固定腔調的
Formo endpoints。預設 `false`，不建立 GPU 資源。設為 `true` 時 CE/Formo image URI、
model-data URL 與 artifact bucket 必須完整；建立 endpoint 與模型 production 核准仍是
兩個獨立條件。

`terraform/asr_lambda_config.tf` 組裝 `ASR_CONFIG_JSON`，Chat Lambda IAM 只允許呼叫已列出的
endpoint。不得以其他 ASR 環境變數補設定。

## 安全摘要

- 音訊只存在記憶體，不寫 `/tmp`、S3 或 DynamoDB。
- 不記錄音訊、逐字稿、HF token、長者個資、endpoint、原始 provider 回應或原始例外。
- Lambda 送給 container 的 metadata 只有 language、sample rate 與 channels。

完整規則依修改範圍按需讀取，不需每次全部載入：

| 修改內容 | 權威文件 |
|---|---|
| `ASR_CONFIG_JSON` | [`config-schema.md`](config-schema.md) |
| SageMaker request/response | [`sagemaker-inference-contract.md`](sagemaker-inference-contract.md) |
| 音訊生命週期、遙測、日誌 | [`security-and-pii.md`](security-and-pii.md) |
| 模型規格與核准狀態 | [`model-catalog.md`](model-catalog.md) |
| 程式檔案職責與測試 | [`backend/src/shared/asr/README.md`](../../backend/src/shared/asr/README.md) |
| 歷史決策 | [`docs/adr/asr-remote-only.md`](../adr/asr-remote-only.md)；只有改架構決策才讀 |

公開 API 仍以 [`docs/api.md`](../api.md) 為準；provider 與 endpoint 細節不得外露。
