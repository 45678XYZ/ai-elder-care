# 智慧長照陪伴 App

長者的 AI 語音陪伴＋照護者的資訊介面。三大模組：**A 語音互動陪伴、B 生活記錄與智慧摘要、C 照護者資訊介面**（做在同一個 App，登入後依角色切換模式）。

- 系統框架：[docs/framework.md](docs/framework.md)
- main 分支 ASR／Bedrock Agent／Frontend 相容性整併計畫：[docs/asr-agentcore-frontend-integration-plan.md](docs/asr-agentcore-frontend-integration-plan.md)
- 對話分塊模型工作流：[docs/feature_segmenter-pairwise-v2.md](docs/feature_segmenter-pairwise-v2.md)
- API 規格：[docs/api.md](docs/api.md)
- ASR 子系統架構：[docs/asr/framework.md](docs/asr/framework.md)
- ASR 模型目錄：[docs/asr/model-catalog.md](docs/asr/model-catalog.md)
- ASR 主備援決策：[docs/adr/asr-managed-transcribe-routing.md](docs/adr/asr-managed-transcribe-routing.md)
- TTS 子系統架構：[docs/tts/framework.md](docs/tts/framework.md)
- TTS 實作計畫：[docs/tts/implementation-plan.md](docs/tts/implementation-plan.md)
- 使用者旅程：[docs/user-journey.md](docs/user-journey.md)
- 交付版使用者旅程：[docs/deliverables/user-journey.md](docs/deliverables/user-journey.md)
- 數據及資料應用說明：[docs/deliverables/data-usage.md](docs/deliverables/data-usage.md)
- 開發流程：[docs/workflow.md](docs/workflow.md)
- 開發慣例：[docs/conventions.md](docs/conventions.md)
- 生活記錄事件萃取移植計畫：[docs/feature_events-extraction.md](docs/feature_events-extraction.md)

## 結構

```text
├── .kiro/          # Kiro 設定與 specs（視需要使用）
├── .agents/        # Codex 專案 skills
├── AGENTS.md       # Codex 全專案工作規範
├── app/            # Flutter（elder/ caregiver/ 兩組頁面 + shared services）
├── asr-lambda/     # CE/Formo SageMaker container 開發與 staging 相容性預檢
├── backend/        # Python Lambda handlers＋agentcore_runtime/ 對話大腦＋ASR/TTS 領域模組＋extraction/ 生活記錄萃取 pipeline
├── terraform/      # API GW, Lambda, DynamoDB, Cognito, EventBridge, S3, Bedrock KB, AgentCore Runtime, Transcribe, SageMaker ASR/TTS
├── data/           # 模擬長者 persona、合成情境腳本、seed 腳本、knowledge/ 衛教文件
├── docs/           # 框架、API、ASR／TTS、ADR、使用者旅程、交付文件、PII、開發流程與功能移植計畫
└── skills/         # 供各 AI 工具開發使用的 skill（開發者需自行加入自己的工具）
```

## 開始開發

- App：見 [app/README.md](app/README.md)（先以 `flutter create` 產生 Android 平台檔案）
- 後端：見 [backend/README.md](backend/README.md)
- 部署：`cd terraform && terraform init && terraform plan`
