# TTS 推論容器

自託管 TTS 模型的 SageMaker inference container 原始碼。Lambda 維持 remote-only，
不在 process 內載入模型，架構見 [`docs/tts/framework.md`](../docs/tts/framework.md)。

所有容器對 Lambda 暴露同一組 endpoint 契約，定義以
[`docs/tts/sagemaker-inference-contract.md`](../docs/tts/sagemaker-inference-contract.md) 為準。

| 目錄 | 模型 | 語言 |
|------|------|------|
| [`breezyvoice/`](breezyvoice/) | `MediaTek-Research/BreezyVoice` | 台灣華語 `zh-TW` |
| [`omnivoice/`](omnivoice/) | `formospeech/omnivoice-hakka-community-1` | 客語六腔 `hak` |

模型 ID、授權與核准狀態統一見 [`docs/tts/model-catalog.md`](../docs/tts/model-catalog.md)。
VoxHakka 依 [`eval/MODEL_SELECTION.md`](../eval/MODEL_SELECTION.md) 的結論（MOS 1.79/5）
不列入部署，尚未建立容器。
