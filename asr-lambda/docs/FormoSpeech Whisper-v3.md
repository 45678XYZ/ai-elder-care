# FormoSpeech Whisper-v3 Container 實作筆記

模型 ID、支援語言、授權、存取方式、prompt allowlist 與核准狀態統一見
[`docs/asr/model-catalog.md`](../../docs/asr/model-catalog.md)。本文件只保留
SageMaker inference container 的實作注意事項。

## 推論格式

- 使用 transformers 載入模型 artifact。
- Container 對 Lambda 的介面必須遵守
  [`docs/asr/sagemaker-inference-contract.md`](../../docs/asr/sagemaker-inference-contract.md)。
- Container 收到 mono／16 kHz／PCM S16LE；不可把模型載入邏輯放進 Lambda。

## Prompt 部署邊界

- Prompt ID 必須固定在 container 環境變數或 SageMaker deployment 設定。
- Lambda request 不得攜帶 prompt ID。
- 尚未選定 prompt 前，Formo endpoint 必須維持未啟用。

## 存取與日誌

- HF token 只可在 artifact 建置或短生命週期 runtime 中使用，不得傳入 Lambda。
- 不得把 HF token、音訊、逐字稿、prompt ID 或 provider 原始回應寫入日誌。
- 模型仍未核准前，本文件不得被視為 production deployment 授權。
