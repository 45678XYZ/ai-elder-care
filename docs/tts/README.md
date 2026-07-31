# TTS 文件導覽

TTS 將 Agent 的繁體中文或客語漢字回覆合成 MP3。公開 API 仍是 `POST /chat`；
provider、endpoint 與模型名稱不對 App 公開。

| 文件 | 用途 |
|---|---|
| [framework.md](framework.md) | 架構、路由、失敗語意與 Agent／Flutter 串接 |
| [config-schema.md](config-schema.md) | `TTS_CONFIG_JSON` schema 與 production gate |
| [model-catalog.md](model-catalog.md) | 中文與客語候選模型、腔調、授權和核准狀態 |
| [security-and-pii.md](security-and-pii.md) | 文字、音訊、日誌、聲紋與授權安全邊界 |
| [sagemaker-inference-contract.md](sagemaker-inference-contract.md) | Lambda 與遠端 TTS container 契約 |
| [implementation-plan.md](implementation-plan.md) | 已確認決策、範圍與驗收計畫 |
| [../adr/tts-remote-only.md](../adr/tts-remote-only.md) | remote-only 與同語言備援決策 |

程式現況見 [`backend/src/shared/tts/README.md`](../../backend/src/shared/tts/README.md)。
