# docs/ — 系統設計與規格文件

本目錄集中管理所有系統設計文件與 API 規格書，是前後端開發的唯一契約來源。開發任何功能前，請先閱讀相關文件以確保實作符合系統設計。

## 文件清單與說明

### 核心架構與 API 規格

| 文件 | 說明 |
|------|------|
| `framework.md` | **系統整體框架**：定義三大模組（語音互動陪伴 / 生活記錄萃取 / 照護者介面）、Mermaid 架構圖、DynamoDB 5 張表設計（elders / conversations / events / daily_summaries / routines）、Session 狀態機（active → closing → closed）、Hybrid realtime/batch 處理策略、AgentCore 長期記憶與寫入規則 |
| `api.md` | **API 規格書**：App 與後端唯一契約。涵蓋所有 REST 端點定義（`POST /chat`、`GET /events`、`GET /summaries`、`CRUD /routines`、`CRUD /elders`、`GET /stats`、session close 等）、認證機制（Cognito JWT）、Request/Response 格式、分頁慣例、錯誤代碼（400/401/403/404/409/429/500）與 enum 定義 |
| `llm_tools.md` | **對話大腦工具規格**：定義 AgentCore Runtime 自動調用的 13 個工具，分四大類——行程管理類（get_today_routines / remind_pending_routines / complete_routine / create_routine / update_routine / deactivate_routine）、事件與摘要類（get_elder_profile / update_elder_profile / get_recent_events）、安全與警報類（notify_caregiver）、衛教知識類（search_health_knowledge）。含觸發意圖、安全冷卻機制與 elder_id 注入說明 |

### 跨團隊需求

| 文件 | 說明 |
|------|------|
| `request_elder-lang-dialect-via-tool.md` | **App → 後端需求**：`update_elder_profile` 請增加 `lang_preference` 與 `hakka_dialect` 兩個參數。長輩目前無法自行更改說話語言與客語腔調——腔調只讀 elder profile，而寫入 profile 的 REST 端點對長者帳號回 403。含為何不鬆綁 `PATCH /elders`、觸發語句範例、誤寫保護建議（腔調寫錯會讓 ASR 聽不懂長輩說話，是會自己鎖死的失效模式），以及「腔調設錯時必須靠打字備援」的邊界情況 |

### 功能設計文件

| 文件 | 說明 |
|------|------|
| `feature_daily-summarization.md` | **每日摘要排程機制**：nightly 深夜生成當日摘要、data_status（partial / complete）二態設計、backfill 等待視窗重算邏輯、覆寫優先序（complete 不被 partial 覆蓋）、LLM 生成文字 vs 程式計算事實的分工原則 |
| `asr-agentcore-frontend-integration-plan.md` | **ASR／AgentCore／Frontend 相容性整併計畫**：三邊介面對齊的過渡步驟與檢查點 |

### 語音子系統（ASR／TTS）

`asr/` 與 `tts/` 兩個子目錄結構相同，各自的 `framework.md` 是該子系統的權威文件。

| 文件 | 說明 |
|------|------|
| `asr/framework.md`、`tts/framework.md` | 子系統架構：remote-only 邊界、provider 路由、失敗時的行為 |
| `asr/model-catalog.md`、`tts/model-catalog.md` | 模型 ID、語言、授權與 production 核准狀態的**唯一來源** |
| `asr/config-schema.md`、`tts/config-schema.md` | Lambda 設定（SSM Parameter）的欄位定義 |
| `asr/sagemaker-inference-contract.md`、`tts/sagemaker-inference-contract.md` | 容器對 Lambda 暴露的 endpoint 契約（`asr-container/`、`tts-container/` 照此實作） |
| `asr/security-and-pii.md`、`tts/security-and-pii.md` | 語音資料的保留、遮蔽與傳輸規範 |
| `tts/implementation-plan.md` | TTS 實作計畫與階段順序 |
| `tts/README.md` | tts/ 子目錄導覽 |

### 架構決策紀錄（`adr/`）

| 文件 | 說明 |
|------|------|
| `adr/asr-remote-only.md`、`adr/tts-remote-only.md` | Lambda 不在 process 內載入模型的決策與其邊界 |
| `adr/asr-managed-transcribe-routing.md` | `zh-TW` 走 Amazon Transcribe Streaming 的主備援決策 |
| `adr/asr-ce-production-approval.md`、`adr/asr-formo-production-approval.md` | 兩個自託管 ASR 模型的 production 核准狀態與證據要求 |
| `adr/asr-model-validation-template.md` | 新模型申請 production 核准時要交的證據樣板 |

### 給非開發者的概覽

兩份都是上面規格書的白話版。**規格以原始文件為準**，兩邊不一致時改的是概覽這一份。

| 文件 | 說明 |
|------|------|
| `framework-overview.md` | `framework.md` 的高階版：不含 DynamoDB 欄位與狀態機細節 |
| `api-overview.md` | `api.md` 的高階版：講每個端點做什麼，不含 request/response 欄位 |

### 開發規範與指南

| 文件 | 說明 |
|------|------|
| `workflow.md` | **開發流程**：Git 分支策略（main 為主分支、feature/ 與 fix/ 開分支）、Conventional Commits 格式（feat / fix / docs / refactor / test / chore）、PR 規範 |
| `conventions.md` | **開發慣例**：命名規則（snake_case 檔名、lowerCamelCase 變數、PascalCase 類別）、註解語言（繁體中文）、測試要求與 Linter 規範 |
| `local_testing.md` | **本機測試指南**：如何在本地環境建立 DynamoDB Local、執行 Backend pytest 與 Extraction Pipeline 單元測試 |
| `pii.md` | **個資處理政策**：系統中個人識別資訊（PII）的處理規範、資料遮蔽策略與保護措施 |
| `user-journey.md` | **使用者旅程**：長者與照護者的完整操作流程劇本，從註冊、首次設定、日常對話到照護者查看摘要的端到端情境描述 |
