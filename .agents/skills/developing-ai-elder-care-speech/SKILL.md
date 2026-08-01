---
name: developing-ai-elder-care-speech
description: "ASR/TTS 與 Chat 語音串接的薄型維護指引。修改 backend/src/shared/asr/、backend/src/shared/tts/、Chat 語音 bridge、terraform/asr_*、terraform/tts_* 或 docs/asr/、docs/tts/ 時使用；依路徑按需載入規格並保留 remote-only、fail-closed、語言／腔調與 PII 邊界。"
---

# 語音子系統維護

本 skill 與根目錄 `AGENTS.md` 一起使用。先讀本檔，再依修改範圍只讀下表列出的文件；
不要預載 ASR/TTS 全部文件或歷史 ADR。

## 不變量

- ASR 與開源 TTS 模型只在 SageMaker 執行；Lambda 不載入推論框架或模型。
- `ASR_CONFIG_JSON`、`TTS_CONFIG_JSON` 分別是唯一設定來源；AWS Region 除外。
- `lang` 只信任 API；客語六腔只信任 elder profile。ASR 不傳 Formo prompt ID。
- 未核准、能力不符或設定矛盾一律 fail closed。客語 TTS 不得 fallback 到中文。
- 不記錄音訊、逐字稿／合成文字、長者個資、token、endpoint、原始 provider 回應或原始例外。
- TTS 模型核准使用 staging/runtime evidence，不建立 Colab 驗證流程。
- 公開 request/response 或錯誤有變更時，同步 `docs/api.md` 與 Flutter DTO/tests。

## 按需閱讀

| 修改範圍 | 必讀文件 |
|---|---|
| ASR router／facade／composition／Chat bridge | `docs/asr/framework.md`、`backend/src/shared/asr/README.md` |
| ASR config 或 `terraform/asr_lambda_config.tf` | `docs/asr/config-schema.md` |
| ASR provider／container I/O | `docs/asr/sagemaker-inference-contract.md` |
| ASR telemetry／logging／audio lifecycle | `docs/asr/security-and-pii.md` |
| TTS router／facade／composition／Chat bridge | `docs/tts/framework.md`、`backend/src/shared/tts/README.md` |
| TTS config 或 `terraform/tts_lambda_config.tf` | `docs/tts/config-schema.md` |
| TTS provider／container I/O | `docs/tts/sagemaker-inference-contract.md` |
| TTS logging／audio storage | `docs/tts/security-and-pii.md` |
| ASR/TTS endpoint Terraform 或模型核准 | 對應 `framework.md`、`model-catalog.md`；只有改決策才讀／改 ADR |
| 僅文件或測試 | 受影響的權威文件／實作即可 |

跨 ASR/TTS 的客語腔調或 Chat 變更，只合併兩列實際相關文件，不讀其他模型目錄或 ADR。

## 驗證

```powershell
cd backend
python -m pytest tests/asr -q       # 修改 ASR 時
python -m pytest tests/tts -q       # 修改 TTS 時
python -m pytest tests/asr/test_chat_asr_bridge.py tests/test_chat.py -q  # 修改 Chat bridge 時
python -m pytest tests/test_elders.py -q  # 修改 profile 語言／腔調時
```

跨 ASR/TTS 行為變更須跑兩個領域 suite；公開欄位或 Flutter DTO 變更另在 `app/` 執行
`dart format --output=none --set-exit-if-changed .`、`flutter analyze`、`flutter test`。

修改 Terraform 時在 `terraform/` 執行 `tofu fmt -check -recursive`、
`tofu init -backend=false`、`tofu validate`，並恢復 OpenTofu 改寫的 `.terraform.lock.hcl`。
不可執行 apply/destroy；不可刪除 `asr-lambda/environment.yml`。
