---
name: developing-ai-elder-care-tts
description: "TTS 子系統維護指引：中／客語路由、客語六腔同步、remote-only 模型、AWS Polly、設定來源、安全邊界與驗證命令。修改 backend/src/shared/tts/、TTS 的 Chat 串接、terraform/tts_models.tf 或 docs/tts/ 時啟用。"
---

# TTS 子系統 — Agent 護欄

本 skill 與根目錄 `AGENTS.md` 一起使用；跨 ASR 修改時還要載入
`developing-ai-elder-care-asr`，且既有 ASR remote-only 禁則優先。

## 閱讀順序

1. `docs/tts/framework.md`
2. `docs/tts/config-schema.md`
3. `docs/tts/security-and-pii.md`
4. `docs/tts/sagemaker-inference-contract.md`
5. `docs/tts/model-catalog.md`
6. `backend/src/shared/tts/README.md`
7. `docs/adr/tts-remote-only.md`

## 絕對禁則

1. 開源 TTS 模型只在 SageMaker endpoint 執行；Chat Lambda 不可下載或載入模型。
2. `TTS_CONFIG_JSON` 是唯一 TTS 路由與 provider 設定來源；AWS Region除外。
3. `lang` 由 API 契約決定；不可從漢字內容猜測中文或客語。
4. `hak` 腔調只可來自已授權 elder profile；呼叫端不得逐 turn 覆寫。
5. 客語 provider 失敗不可回落到中文 provider；全數失敗回文字與 null 音訊。
6. 未核准、授權不符、能力不支援或設定矛盾的 route 一律 fail closed。
7. 不可記錄合成文字、音訊 bytes、elder ID、HF token、endpoint 或原始 provider 回應。
8. 不建立 Colab 驗證流程；模型核准使用 staging/runtime evidence。

## 固定語言與腔調

- `zh-TW`：輸入與 Agent 回覆使用繁體中文；優先台灣華語 provider。
- Polly Zhiyu 是 `cmn-CN` 相容／備援 provider，不得標示為台灣口音。
- `hak`：`htia_sixian`、`htia_hailu`、`htia_dapu`、`htia_raoping`、
  `htia_zhaoan`、`htia_nansixian`。
- VoxHakka 不支援南四縣；該腔調不可把它放入 fallback chain。

## 文件同步

| 修改 | 同時檢查 |
|---|---|
| TTS config/schema | `docs/tts/config-schema.md`、Terraform contract tests |
| remote endpoint I/O | `docs/tts/sagemaker-inference-contract.md` |
| telemetry | `docs/tts/security-and-pii.md` |
| routing/composition | `docs/tts/framework.md`、領域 README |
| 公開欄位或錯誤 | `docs/api.md`、Flutter DTO/tests |
| 客語腔調 ASR 同步 | ASR framework/config/contract/ADR 與測試 |
| 新增／移動文件 | 根 README 索引、相關交叉連結 |

## 驗證

```powershell
cd backend
python -m pytest tests/tts -q
python -m pytest tests/test_chat.py tests/test_elders.py -q
python -m pytest tests/asr -q
python -m pytest -q

cd ../terraform
tofu fmt -check -recursive
tofu init -backend=false
tofu validate
# 完成後恢復 OpenTofu 改寫的 .terraform.lock.hcl，交付物維持 Terraform 格式

cd ../app
dart format --output=none --set-exit-if-changed .
flutter analyze
flutter test
```

OpenTofu／Terraform 只做 format、validate、plan；不可執行 apply 或 destroy。使用 OpenTofu
後必須依根 `AGENTS.md` 恢復 `.terraform.lock.hcl` 的 Terraform registry／hash。
