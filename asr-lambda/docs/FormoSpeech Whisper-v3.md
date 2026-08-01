# FormoSpeech Whisper-v3 Container 實作筆記

模型 ID、支援語言、授權、存取方式、prompt allowlist 與核准狀態統一見
[`docs/asr/model-catalog.md`](../../docs/asr/model-catalog.md)。本文件只保留
SageMaker inference container 的實作注意事項。

## 推論格式

- 使用 transformers 載入模型 artifact。
- Container 對 Lambda 的介面必須遵守
  [`docs/asr/sagemaker-inference-contract.md`](../../docs/asr/sagemaker-inference-contract.md)。
- Container 收到 mono／16 kHz／PCM S16LE；不可把模型載入邏輯放進 Lambda。

## Prompt 與漢字解碼部署邊界

- 六個 endpoints 的 `FORMO_PROMPT_ID` 必須分別固定為對應的六腔 wire value；不得在
  request 時切換。
- 每個 endpoint 固定 `FORMO_GENERATION_LANGUAGE=Chinese`，讓 Whisper 輸出客語漢字。
  這不是中文 ASR capability；Formo 仍只允許 `hak` route。
- Lambda request 不得攜帶 prompt ID 或 generation language。

## 存取與日誌

- HF token 只可在本機／CI 的 artifact 建置階段以 secret 短暫注入；不得傳入 Lambda 或
  SageMaker environment。
- 不得把 HF token、音訊、逐字稿、prompt ID 或 provider 原始回應寫入日誌。
- Gated access 已取得，但模型授權、指定 instance staging 與 runtime 容量仍未核准；本文件
  不得被視為 production deployment 授權。
