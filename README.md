# 智慧長照陪伴 App

長者的 AI 語音陪伴＋照護者的資訊介面。三大模組：**A 語音互動陪伴、B 生活記錄與智慧摘要、C 照護者資訊介面**（做在同一個 App，登入後依角色切換模式）。

- 系統框架：[docs/framework.md](docs/framework.md)
- API 規格：[docs/api.md](docs/api.md)
- ASR 遠端化遷移計畫：[docs/asr/remote-only-migration-plan.md](docs/asr/remote-only-migration-plan.md)
- 開發流程：[docs/workflow.md](docs/workflow.md)
- 開發慣例：[docs/conventions.md](docs/conventions.md)

## 結構

```text
├── .kiro/          # Kiro 設定與 specs（視需要使用）
├── app/            # Flutter（elder/ caregiver/ 兩組頁面 + shared services）
├── asr-lambda/     # ASR 模型規格文件與本機 conda 環境
├── backend/        # Python Lambda handlers（chat, summary, apis）＋ ASR 領域模組
├── terraform/      # API GW, Lambda, DynamoDB, Cognito, EventBridge, S3, Bedrock KB, SageMaker ASR
├── data/           # 模擬長者 persona、情境對話腳本、seed 腳本、knowledge/ 衛教文件
├── docs/           # 框架、API 規格、使用者旅程、PII 說明、asr/ 文件、adr/ 決策紀錄、開發流程與慣例
└── skills/         # 供各 AI 工具開發使用的 skill（開發者需自行加入自己的工具）
```

## 開始開發

- App：見 [app/README.md](app/README.md)（先以 `flutter create` 產生 Android 平台檔案）
- 後端：見 [backend/README.md](backend/README.md)
- 部署：`cd terraform && terraform init && terraform plan`
