# 客照e點通 App

這是一套專為高齡長者與其家屬設計的**智慧長照解決方案**。系統結合生成式 AI 技術，打造具備情感陪伴、行程提醒與異常偵測的智慧助理，並為家屬提供清晰、即時的照護儀表板。

## 系統三大核心模組

本專案採單一 Flutter App 實作，使用者登入後依據角色（長者 / 照護者）切換對應模式：

| 模組 | 名稱 | 說明 |
|------|------|------|
| A | 語音互動陪伴 | 全語音互動介面、AgentCore Runtime 對話大腦（長期記憶 + 溫暖語氣）、自動追蹤行程完成、衛教知識庫問答 |
| B | 生活記錄與智慧摘要 | AI 從自然對話自動萃取結構化生活事件、每日定時產出身心靈摘要報告 |
| C | 照護者資訊介面 | 即時緊急警報、行程管理（用藥/回診/運動）、AI 每日健康摘要、事件時間軸與統計 |

## 技術架構

```
Flutter App（長者語音 + 照護者管理）
        │
        ▼
API Gateway + Cognito JWT 認證
        │
        ├── POST /chat ──→ AgentCore Runtime（LangGraph 狀態機 + 13 個工具）
        │                       ├── Tools Lambda（行程/事件/安全通知）
        │                       └── Bedrock Knowledge Base（衛教知識檢索）
        │
        ├── REST APIs ──→ Lambda Handlers（elders / routines / events / summaries / stats）
        │
        ├── Session Close ──→ SQS ──→ Batch Extractor（Extraction Pipeline）
        │
        └── EventBridge ──→ Summary Generator（每日摘要）/ Daily Digest（晚報推播）
```

**核心技術選型**：
- **前端**：Flutter（單一 App 雙模式）
- **後端**：Python Lambda + LangGraph + Bedrock Claude
- **基礎設施**：Terraform IaC（API Gateway / DynamoDB / Cognito / S3 / SQS / EventBridge / SNS）
- **對話 AI**：AWS Bedrock AgentCore Runtime + LangChain 工具鏈
- **語音**：裝置端 ASR + 後端 TTS（Polly 中文 / OmniVoice 客語）

## 專案目錄結構

```text
├── app/            # Flutter（elder/ caregiver/ 兩組頁面 + shared services）
├── asr-lambda/     # CE/Formo SageMaker container 開發與 staging 相容性預檢
├── asr-container/  # ASR 推論容器原始碼（FormoSpeech 客語六腔）
├── tts-container/  # TTS 推論容器原始碼（BreezyVoice 台灣華語、OmniVoice 客語）
├── backend/        # Python Lambda handlers＋agentcore_runtime/ 對話大腦＋ASR/TTS 領域模組＋extraction/ 生活記錄萃取 pipeline
├── terraform/      # API GW, Lambda, DynamoDB, Cognito, EventBridge, S3, Bedrock KB, AgentCore Runtime, Transcribe, SageMaker ASR/TTS
├── data/           # 模擬長者 persona、合成情境腳本、seed 腳本、knowledge/ 衛教文件
├── docs/           # 框架、API、ASR／TTS、ADR、使用者旅程、交付文件、PII、開發流程與功能移植計畫
├── experiments/    # 實驗性 PoC（RAG 檢索驗證）
├── scripts/        # 全域工具腳本（知識庫上傳等）
└── skills/         # 供各 AI 工具開發使用的 skill（開發者需自行加入自己的工具）
```

各目錄皆有獨立 README 說明其內部檔案職責，請見：
- [app/README.md](app/README.md) — Flutter App 完整架構與檔案說明
- [backend/src/README.md](backend/src/README.md) — 後端核心原始碼逐檔說明
- [data/README.md](data/README.md) — 資料資源（Persona / 知識庫 / 測試情境）
- [docs/README.md](docs/README.md) — 設計文件導覽
- [asr-container/README.md](asr-container/README.md) — ASR 推論容器與部署 runbook
- [tts-container/README.md](tts-container/README.md) — TTS 推論容器與部署 runbook

## 文件導覽

### 核心架構與規格
| 文件 | 說明 |
|------|------|
| [docs/framework.md](docs/framework.md) | 系統整體框架：架構圖、模組設計、DynamoDB 表結構、Session 狀態機 |
| [docs/api.md](docs/api.md) | API 規格書：所有 REST 端點定義（App 與後端唯一契約） |
| [docs/llm_tools.md](docs/llm_tools.md) | 對話大腦工具規格：13 個工具的觸發條件與 I/O |

### 語音子系統
| 文件 | 說明 |
|------|------|
| [docs/asr/framework.md](docs/asr/framework.md) | ASR 子系統架構 |
| [docs/asr/model-catalog.md](docs/asr/model-catalog.md) | ASR 模型目錄 |
| [docs/adr/asr-managed-transcribe-routing.md](docs/adr/asr-managed-transcribe-routing.md) | ASR 主備援決策 |
| [docs/tts/framework.md](docs/tts/framework.md) | TTS 子系統架構 |
| [docs/tts/implementation-plan.md](docs/tts/implementation-plan.md) | TTS 實作計畫 |

### 功能設計
| 文件 | 說明 |
|------|------|
| [docs/feature_events-extraction.md](docs/feature_events-extraction.md) | 生活事件萃取：從對話到結構化資料的完整流程 |
| [docs/feature_segmenter-pairwise-v2.md](docs/feature_segmenter-pairwise-v2.md) | 對話分塊演算法 V2：embedding 相似度主題切割 |
| [docs/feature_daily-summarization.md](docs/feature_daily-summarization.md) | 每日摘要機制：排程生成、partial/complete 狀態、backfill |
| [docs/asr-agentcore-frontend-integration-plan.md](docs/asr-agentcore-frontend-integration-plan.md) | ASR／Bedrock Agent／Frontend 相容性整併計畫 |

### 交付文件
| 文件 | 說明 |
|------|------|
| [docs/user-journey.md](docs/user-journey.md) | 使用者旅程 |
| [docs/deliverables/user-journey.md](docs/deliverables/user-journey.md) | 交付版使用者旅程 |
| [docs/deliverables/data-usage.md](docs/deliverables/data-usage.md) | 數據及資料應用說明 |

### 開發指南
| 文件 | 說明 |
|------|------|
| [docs/workflow.md](docs/workflow.md) | 開發流程：分支策略、Commit 慣例 |
| [docs/conventions.md](docs/conventions.md) | 開發慣例：命名、註解、測試規範 |
| [docs/local_testing.md](docs/local_testing.md) | 本機測試指南 |

## 快速開始

### 1. 前端 App (Flutter)

平台檔案（`android/`、`web/`）已在版控內，**不要跑 `flutter create`**——它會用模板覆蓋掉現有設定，其中麥克風權限、通知 receiver 與 `<queries>` 掉了不會報錯，只會變成按了沒反應。

```bash
cd app
flutter pub get
flutter run
```

### 2. 雲端後端 (Python)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest                 # 執行測試
```

AgentCore Runtime 額外依賴：
```bash
pip install -r agentcore_requirements.txt
```

### 3. 基礎設施 (Terraform)

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### 4. 環境變數

複製 `.env.example` 為 `.env` 並填入 AWS 憑證與服務設定：
```bash
cp .env.example .env
```

## terraform/ — 基礎設施

| 檔案 | 管理的 AWS 資源 |
|------|-----------------|
| `cognito.tf` | Cognito User Pool（使用者認證與 JWT 發放） |
| `api_gateway.tf` | API Gateway REST API（路由與 JWT 驗證） |
| `lambda.tf` | Lambda Functions（chat / tools / elders / routines / events / summaries / stats / session_closer / batch_extractor / dlq_reconciler / daily_digest / summary_generator / pre_token / post_confirmation） |
| `dynamodb.tf` | DynamoDB Tables（elders / conversations / events / daily_summaries / routines / elder_accounts） |
| `sqs.tf` | SQS Queue + DLQ（batch 事件萃取佇列） |
| `eventbridge.tf` | EventBridge Scheduler（idle session close / nightly summary / backfill / daily digest） |
| `s3.tf` | S3 Bucket（TTS 音訊檔存放） |
| `s3_vectors.tf` | S3 Bucket（Knowledge Base 向量索引源文件） |
| `bedrock_kb.tf` | Bedrock Knowledge Base（衛教知識庫） |
| `agentcore.tf` | AgentCore Runtime（對話大腦部署配置） |
| `cloudwatch.tf` | CloudWatch Alarms + SNS（監控告警） |
| `providers.tf` | AWS Provider 設定 |
| `variables.tf` | 輸入變數定義 |
| `outputs.tf` | 輸出值定義 |
| `versions.tf` | Terraform 與 Provider 版本鎖定 |

## experiments/ — 實驗性 PoC

| 子目錄 | 說明 |
|--------|------|
| `rag-poc/` | 衛教知識庫 RAG 問答驗證：用 Chroma + BM25 + Reranker 在本機跑通檢索邏輯。驗證完成後正式版由 Bedrock Knowledge Base 取代 |

## scripts/ — 全域工具腳本

| 檔案 | 說明 |
|------|------|
| `build_kb_upload.py` | 將 `data/knowledge/` 衛教文件轉換為 Bedrock KB 上傳格式（正文 + metadata sidecar） |
| `sync_kb.sh` | 同步知識庫文件至 S3 並觸發 Bedrock KB 重新索引 |
