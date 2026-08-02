# docs/ — 系統設計與規格文件

本目錄集中管理所有系統設計文件與 API 規格書，是前後端開發的唯一契約來源。開發任何功能前，請先閱讀相關文件以確保實作符合系統設計。

## 文件清單與說明

### 核心架構與 API 規格

| 文件 | 說明 |
|------|------|
| `framework.md` | **系統整體框架**：定義三大模組（語音互動陪伴 / 生活記錄萃取 / 照護者介面）、Mermaid 架構圖、DynamoDB 5 張表設計（elders / conversations / events / daily_summaries / routines）、Session 狀態機（active → closing → closed）、Hybrid realtime/batch 處理策略、AgentCore 長期記憶與寫入規則 |
| `api.md` | **API 規格書**：App 與後端唯一契約。涵蓋所有 REST 端點定義（`POST /chat`、`GET /events`、`GET /summaries`、`CRUD /routines`、`CRUD /elders`、`GET /stats`、session close 等）、認證機制（Cognito JWT）、Request/Response 格式、分頁慣例、錯誤代碼（400/401/403/404/409/429/500）與 enum 定義 |
| `llm_tools.md` | **對話大腦工具規格**：定義 AgentCore Runtime 自動調用的 13 個工具，分四大類——行程管理類（get_today_routines / remind_pending_routines / complete_routine / create_routine / update_routine / deactivate_routine）、事件與摘要類（get_elder_profile / update_elder_profile / get_recent_events）、安全與警報類（notify_caregiver）、衛教知識類（search_health_knowledge）。含觸發意圖、安全冷卻機制與 elder_id 注入說明 |
| `framework-overview.md`、`api-overview.md` | 上面兩份權威文件的易讀導覽版，適合初次了解系統的人；技術細節仍以原始規格為準 |

### 目錄配置

`docs/` 根目錄只放跨系統的權威文件與開發規範；功能與子系統規格集中在 `docs/features/`：

| 位置 | 內容 |
|------|------|
| `features/asr/` | ASR 子系統架構、設定 schema、模型目錄、SageMaker 推論契約、安全與 PII |
| `features/tts/` | TTS 子系統同上 |
| `features/adr/` | remote-only、Transcribe 主備援路由、CE／Formo 模型核准等決策紀錄 |
| `features/model_selection_asr_tts.md` | ASR／TTS 模型選型比較 |
| `deliverables/` | 交付版使用者旅程與前端資料流說明 |

### 跨團隊需求

| 文件 | 說明 |
|------|------|
| `features/request_elder-lang-dialect-via-tool.md` | **App → 後端需求**：`update_elder_profile` 請增加 `lang_preference` 與 `hakka_dialect` 兩個參數。長輩目前無法自行更改說話語言與客語腔調——腔調只讀 elder profile，而寫入 profile 的 REST 端點對長者帳號回 403。含為何不鬆綁 `PATCH /elders`、觸發語句範例、誤寫保護建議（腔調寫錯會讓 ASR 聽不懂長輩說話，是會自己鎖死的失效模式），以及「腔調設錯時必須靠打字備援」的邊界情況 |

### 功能設計文件

| 文件 | 說明 |
|------|------|
| `features/feature_daily-summarization.md` | **每日摘要排程機制**：nightly 深夜生成當日摘要、data_status（partial / complete）二態設計、backfill 等待視窗重算邏輯、覆寫優先序（complete 不被 partial 覆蓋）、LLM 生成文字 vs 程式計算事實的分工原則 |
| `features/asr-agentcore-frontend-integration-plan.md` | **ASR／Bedrock Agent／Frontend 相容性整併計畫**：三方介面對齊的步驟與待辦 |

> 生活事件萃取的 pipeline 設計（`direct_seven`：不分塊、不檢索，依 turn 邊界分批做七大類萃取）直接寫在 `framework.md` 的「Direct Seven Pipeline」與「events 表」兩節，沒有獨立文件。

### 開發規範與指南

| 文件 | 說明 |
|------|------|
| `workflow.md` | **開發流程**：Git 分支策略（main 為主分支、feature/ 與 fix/ 開分支）、Conventional Commits 格式（feat / fix / docs / refactor / test / chore）、PR 規範 |
| `conventions.md` | **開發慣例**：命名規則（snake_case 檔名、lowerCamelCase 變數、PascalCase 類別）、註解語言（繁體中文）、測試要求與 Linter 規範 |
| `local_testing.md` | **本機測試指南**：如何在本地環境建立 DynamoDB Local、執行 Backend pytest 與 Extraction Pipeline 單元測試 |
| `pii.md` | **個資處理政策**：系統中個人識別資訊（PII）的處理規範、資料遮蔽策略與保護措施 |
| `user-journey.md` | **使用者旅程**：長者與照護者的完整操作流程劇本，從註冊、首次設定、日常對話到照護者查看摘要的端到端情境描述 |
