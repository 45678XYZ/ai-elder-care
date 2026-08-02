# ASR 子系統框架

## 目標與邊界

`POST /chat` 的 audio 輸入由 Chat Lambda 解碼，再交給 ASR。ASR 只負責把音訊轉成
非空白逐字稿或 typed error；它不認識 HTTP、認證、session、資料庫或聊天流程。

ASR 採 remote-only：Lambda 只正規化音訊並呼叫受控的 AWS 遠端服務，不下載、載入或
執行模型。遠端服務可以是 Amazon Transcribe Streaming 或 SageMaker real-time endpoint。
`ASR_CONFIG_JSON` 是唯一 ASR 設定來源（AWS Region 除外）；設定不完整、路由停用或
自託管模型未核准時，一律 fail closed 且不得把未核准模型當成備援。

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
AsrRouter ── enabled route → approved/capable providers → same-language fallback
    │
    ├─ zh-TW：Amazon Transcribe Streaming → CE SageMaker endpoint
    ├─ hak:<六腔>：對應 Formo SageMaker endpoint → 共用 CE SageMaker endpoint
    └─ hak_mock（僅單元測試與明確本機開發）
    │
    ▼
Transcript | TypedAsrError
```

## 路由規則

- 中文 route key 固定為 `zh-TW`，provider 順序固定為
  `amazon_transcribe_zh_tw` → `ce_remote`。
- 客語 production route key 為 `hak:<profile dialect>`；六腔 wire values 見
  [`config-schema.md`](config-schema.md)。每腔以對應 Formo endpoint 為主力，
  `ce_remote` 是六腔共同備援。generic `hak` 只保留給本機 mock 相容。
- Amazon Transcribe provider 固定使用 Streaming、`zh-TW`、16 kHz、PCM；只合併 final
  transcript，不使用 batch transcription 或 S3 暫存。
- Formo prompt 固定在對應腔調的 endpoint/container；Lambda request 不傳 prompt ID。
  Container 另固定 `FORMO_GENERATION_LANGUAGE=Chinese`，表示 Whisper 的漢字解碼設定，
  不得把 Formo 的能力誤登記為 `zh-TW`。
- 只有 `provider_unavailable`、`provider_failure`、`provider_invalid_response` 可嘗試下一個
  provider。取消、逾期、音訊、語言、設定或核准錯誤立即終止。
- `default_config()` 只讓 `hak_mock` 可用。Terraform 會注入明確 production 設定，因此
  部署環境不會意外使用 mock。

## 責任切分

| 元件 | 責任 |
|---|---|
| Flutter | 錄音、base64 編碼、送出 API；不可指定 provider |
| Chat handler | 解碼、取得 profile 腔調、設定 deadline、對映公開錯誤 |
| ASR facade/audio | 輸入門檻、正規化、終態遙測 |
| ASR router/providers | 設定驅動路由、核准 gate、同語言 fallback、Transcribe/SageMaker adapter |
| Amazon Transcribe | 以 `zh-TW` Streaming 接收 PCM chunk 並回傳 final transcript events |
| SageMaker container | 固定模型與部署設定、推論、回傳 `{ "text": "..." }` |

## 基礎設施

`terraform/asr_models.tf` 以 `asr_enable_endpoints` 控制一個 CE endpoint 與六個固定腔調的
Formo endpoints。啟用時固定每個 endpoint 一台，不建立 autoscaling：

| Endpoint | Instance type |
|---|---|
| Formo 四縣、海陸 | `ml.g5.2xlarge` |
| Formo 大埔、饒平 | `ml.g5.xlarge` |
| Formo 詔安、南四縣 | `ml.g4dn.2xlarge` |
| CE 共用備援 | `ml.g5.4xlarge` |

`asr_enable_endpoints=false` 時不建立 SageMaker GPU 資源；中文的受控 Transcribe route
不依賴此開關。啟用 SageMaker endpoints 時，image URI、model-data URL 與 artifact bucket
必須完整；建立 endpoint 與模型 production 核准仍是兩個獨立條件。

`terraform/asr_lambda_config.tf` 組裝 `ASR_CONFIG_JSON`。Chat Lambda IAM 只允許
`transcribe:StartStreamTranscription` 與呼叫明列的 SageMaker endpoints；不得以其他 ASR
環境變數補設定。

## 安全摘要

- 音訊只存在記憶體，不寫 `/tmp`、S3 或 DynamoDB。
- Transcribe 使用記憶體內 streaming，不建立 transcription job 或輸出 bucket。
- 不記錄音訊、逐字稿、HF token、長者個資、endpoint、原始 provider 回應或原始例外。
- Lambda 送給 SageMaker container 的 metadata 只有 language、sample rate 與 channels。
- 競賽環境只使用合成音訊、模擬 persona 與非真實健康內容，不匯入真實長者資料。

完整規則依修改範圍按需讀取：

| 修改內容 | 權威文件 |
|---|---|
| `ASR_CONFIG_JSON` | [`config-schema.md`](config-schema.md) |
| Transcribe／SageMaker request/response | [`sagemaker-inference-contract.md`](sagemaker-inference-contract.md) |
| 音訊生命週期、遙測、日誌 | [`security-and-pii.md`](security-and-pii.md) |
| 模型規格與核准狀態 | [`model-catalog.md`](model-catalog.md) |
| 程式檔案職責與測試 | [`backend/src/shared/asr/README.md`](../../../backend/src/shared/asr/README.md) |
| 現行架構決策 | [`docs/adr/asr-managed-transcribe-routing.md`](../adr/asr-managed-transcribe-routing.md) |

公開 API 仍以 [`docs/api.md`](../../api.md) 為準；provider 與 endpoint 細節不得外露。
