# ASR 推論容器

自託管 ASR 模型的 SageMaker inference container 原始碼。Lambda 維持 remote-only，
不在 process 內載入模型，架構見 [`docs/asr/framework.md`](../docs/features/asr/framework.md)。

所有容器對 Lambda 暴露同一組 endpoint 契約，定義以
[`docs/asr/sagemaker-inference-contract.md`](../docs/features/asr/sagemaker-inference-contract.md) 為準。

| 目錄 | 模型 | 語言 |
|------|------|------|
| [`formospeech/`](formospeech/) | `formospeech/whisper-large-v3-taiwanese-hakka` | 客語六腔 `hak` |

模型 ID、授權與核准狀態統一見 [`docs/asr/model-catalog.md`](../docs/features/asr/model-catalog.md)。

`zh-TW` 走 Amazon Transcribe Streaming（AWS managed），沒有容器。Taiwan-Tongues-ASR-CE
依模型選型結論（見 [`docs/features/model_selection_asr_tts.md`](../docs/features/model_selection_asr_tts.md)）不列入部署，尚未建立容器。
