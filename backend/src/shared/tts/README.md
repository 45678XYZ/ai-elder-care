# TTS 領域套件

依修改範圍按需閱讀：路由／組裝見 [`docs/tts/framework.md`](../../../../docs/tts/framework.md)，
設定見 [`config-schema.md`](../../../../docs/tts/config-schema.md)，provider I/O 見
[`sagemaker-inference-contract.md`](../../../../docs/tts/sagemaker-inference-contract.md)。

| 檔案 | 職責 |
|---|---|
| `types.py` | 語言、六腔、deadline、取消、成功與 typed error |
| `config.py` | schema v1、provider/route、模型 production gate |
| `providers.py` | Polly、SageMaker 與測試 mock adapter |
| `router.py` | 輸入門檻、單一入口、能力 gate 與同語言 fallback |
| `composition.py` | `TTS_CONFIG_JSON` 載入、受控模型 registry、warm-start facade |

絕對規則：remote-only；不從文字猜語言；客語必須有 profile 六腔；不做客語→中文
fallback；不記錄文字或音訊；未核准模型不建立 provider；TTS 失敗由 Chat 轉成 nullable
audio，不改變已成立的文字 turn。
