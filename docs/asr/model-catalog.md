# ASR 模型目錄

本文件是 ASR 模型固定規格與生命週期狀態的唯一權威來源。Colab 驗證套件、
SageMaker container 文件、Terraform 與程式碼只保留各自的操作或實作細節，
不重複維護下列模型資料。

相關文件：

- 架構入口：[`docs/asr/framework.md`](./framework.md)
- 設定規格：[`docs/asr/config-schema.md`](./config-schema.md)
- SageMaker 契約：[`docs/asr/sagemaker-inference-contract.md`](./sagemaker-inference-contract.md)
- Colab 驗證流程：[`backend/asr_colab/README.md`](../../backend/asr_colab/README.md)

## 模型清單

| Metadata key | Model ID／revision | ASR 語言 | 推論框架 | 授權／存取 | 目前狀態 | 核准紀錄 |
|---|---|---|---|---|---|---|
| `taiwan_tongues_ce` | `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0`／`v2.0` | `zh-TW`、`hak` | CTranslate2／faster-whisper | `other`／open | `colab_validation_only`、`not_approved` | [`asr-ce-production-approval.md`](../adr/asr-ce-production-approval.md) |
| `formospeech_whisper_v3` | `formospeech/whisper-large-v3-taiwanese-hakka`／`main` | `hak` | transformers | CC BY-NC 4.0／gated | `colab_validation_only`、`not_approved` | [`asr-formo-production-approval.md`](../adr/asr-formo-production-approval.md) |

## 模型用途

### Taiwan-Tongues-ASR-CE

- 預定作為 `zh-TW` 遠端主力，並可作為 `hak` 備援。
- 模型卡標示輸出文字使用的語言不保證；現行 Lambda contract 只驗證非空白文字。
- 授權標示為 `other`，production 前必須取得可用於本專案用途的明確核准。

模型來源：

- [Hugging Face](https://huggingface.co/adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0)
- [GitHub](https://github.com/adi-gov-tw/Taiwan-Tongues-ASR-CE)

### FormoSpeech Whisper-v3

- 僅支援 `hak`，預定作為客語遠端備援。
- 模型是 gated model；下載與驗證需要已獲授權的 Hugging Face 帳號。
- 授權為 CC BY-NC 4.0，只允許非商業用途；專案轉為商業用途時不得核准
  `license_cleared`。
- 方言 prompt 固定在 SageMaker container／部署設定，Lambda 不傳 prompt ID。

允許的部署 prompt：

- `htia_sixian`
- `htia_hailu`
- `htia_dapu`
- `htia_raoping`
- `htia_zhaoan`
- `htia_nansixian`

模型來源：

- [Hugging Face](https://huggingface.co/formospeech/whisper-large-v3-taiwanese-hakka)

## 生命週期規則

模型從驗證到 production 的狀態固定依序為：

1. `colab_validation_only`／`not_approved`：只能人工驗證，不得 production invocation。
2. 個別模型 ADR 補齊 Colab、授權、存取、額度與容量證據。
3. ADR 正式核准後，以受審查的程式碼變更將 `usage_restriction`、
   `approval_state`、五項 production gate 與 `approval_record_ref` 一次更新。
4. 任一條件失效時，立即撤回核准並使路由 fail closed。

建立 SageMaker endpoint 不代表模型已核准。只有設定、ADR 與五項 gate 同時成立，
Lambda 才能建立 remote provider 並呼叫模型。
