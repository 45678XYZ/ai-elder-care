# ASR 模型與服務目錄

本文件是 ASR provider 固定規格與自託管模型生命週期狀態的唯一權威來源。
SageMaker container 文件、Terraform 與程式碼只保留各自的操作或實作細節，不重複維護
下列模型資料。

相關文件：

- 架構入口：[`docs/asr/framework.md`](./framework.md)
- 設定規格：[`docs/asr/config-schema.md`](./config-schema.md)
- Provider 契約：[`docs/asr/sagemaker-inference-contract.md`](./sagemaker-inference-contract.md)
- 核准 ADR 範本：[`docs/adr/asr-model-validation-template.md`](../adr/asr-model-validation-template.md)

## Provider 清單

| Provider／metadata key | Model ID／revision | ASR 語言 | 推論方式 | 授權／存取 | 目前狀態 | 核准紀錄 |
|---|---|---|---|---|---|---|
| `amazon_transcribe_zh_tw` | Amazon Transcribe Streaming | `zh-TW` | AWS managed streaming | AWS service／IAM | 中文主力；受控 allowlist | [`asr-managed-transcribe-routing.md`](../adr/asr-managed-transcribe-routing.md) |
| `taiwan_tongues_ce` | `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0`／`v2.0` | `zh-TW`、`hak` | CTranslate2／faster-whisper | `other`／open | `staging_validation_only`、`not_approved`；共同備援 | [`asr-ce-production-approval.md`](../adr/asr-ce-production-approval.md) |
| `formospeech_whisper_v3` | `formospeech/whisper-large-v3-taiwanese-hakka`／`main` | `hak` | transformers | CC BY-NC 4.0／gated，存取已取得 | `staging_validation_only`、`not_approved`；六腔主力候選 | [`asr-formo-production-approval.md`](../adr/asr-formo-production-approval.md) |

## Provider 用途

### Amazon Transcribe Streaming

- 作為 `zh-TW` 主力；固定 provider ID `amazon_transcribe_zh_tw`。
- Provider 內部鎖定 `zh-TW`、PCM、16 kHz 與 final transcript，不接受設定覆寫。
- 全程 memory-only streaming，不建立 batch job，也不把音訊或逐字稿寫入 S3。
- 失敗時只可依 router 規則 fallback 到已核准的 CE；不得 fallback 到 Formo。

### Taiwan-Tongues-ASR-CE

- 作為 `zh-TW` 與六腔 `hak` 的共用 SageMaker 備援，不再是中文主力。
- 模型卡標示輸出文字使用的語言不保證；Lambda contract 只驗證非空白文字，因此 staging
  必須分語言驗收辨識品質。
- 授權標示為 `other`；production 前必須取得可用於本專案用途的明確核准。
- 固定部署於一台 `ml.g5.4xlarge`，不建立 autoscaling。

模型來源：

- [Hugging Face](https://huggingface.co/adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0)
- [GitHub](https://github.com/adi-gov-tw/Taiwan-Tongues-ASR-CE)

### FormoSpeech Whisper-v3

- 僅支援 `hak`；六腔各使用一個固定 prompt 的 SageMaker endpoint，作為該腔主力。
- gated repository 的存取權已取得；這只滿足 `access_granted`，不代表授權、runtime 或
  production 核准已完成。
- 授權為 CC BY-NC 4.0，只允許非商業用途；專案轉為商業用途時不得核准
  `license_cleared`，除非另取得適用授權。
- 每個 endpoint 固定 `FORMO_PROMPT_ID` 與 `FORMO_GENERATION_LANGUAGE=Chinese`。
  `Chinese` 是 Whisper 的客語漢字解碼設定，不表示模型支援 `zh-TW`；Lambda 不傳這兩個值。

允許的部署 prompt 與 instance：

| Prompt／腔調 | Instance type |
|---|---|
| `htia_sixian`、`htia_hailu` | `ml.g5.2xlarge` |
| `htia_dapu`、`htia_raoping` | `ml.g5.xlarge` |
| `htia_zhaoan`、`htia_nansixian` | `ml.g4dn.2xlarge` |

每個 endpoint 固定一台，不建立 autoscaling。六腔共用同一個映像與同一份 model artifact，
腔調由 `FORMO_PROMPT_ID` 逐 endpoint 注入。推論容器與部署／驗收步驟見
[`asr-container/formospeech/README.md`](../../asr-container/formospeech/README.md)。

模型來源：

- [Hugging Face](https://huggingface.co/formospeech/whisper-large-v3-taiwanese-hakka)

## 生命週期規則

自託管模型從驗證到 production 的狀態固定依序為：

1. `staging_validation_only`／`not_approved`：只可在指定 SageMaker staging/runtime 驗證，
   不得接受 production invocation。
2. 個別模型 ADR 補齊 staging 辨識品質與延遲、授權、存取、配額及實際 runtime 容量證據。
3. ADR 正式核准後，以受審查的程式碼變更將 `usage_restriction`、
   `approval_state`、五項 production gate 與 `approval_record_ref` 一次更新。
4. 任一條件失效時，立即撤回核准並使路由 fail closed。

建立 SageMaker endpoint 不代表模型已核准。只有設定、ADR 與五項 gate 同時成立，Lambda
才能建立 remote provider 並呼叫模型。Amazon Transcribe 不使用自託管模型 gate，但仍受固定
service capability、IAM、deadline、PII 與 typed error 規則約束。
