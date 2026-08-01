# docs/ — 系統設計與規格文件

本目錄集中管理所有系統設計文件與 API 規格書，是前後端開發的唯一契約來源。開發任何功能前，請先閱讀相關文件以確保實作符合系統設計。

## 文件清單與說明

### 核心架構與 API 規格

| 文件 | 說明 |
|------|------|
| `framework.md` | **系統整體框架**：定義三大模組（語音互動陪伴 / 生活記錄萃取 / 照護者介面）、Mermaid 架構圖、DynamoDB 5 張表設計（elders / conversations / events / daily_summaries / routines）、Session 狀態機（active → closing → closed）、Hybrid realtime/batch 處理策略、AgentCore 長期記憶與寫入規則 |
| `api.md` | **API 規格書**：App 與後端唯一契約。涵蓋所有 REST 端點定義（`POST /chat`、`GET /events`、`GET /summaries`、`CRUD /routines`、`CRUD /elders`、`GET /stats`、session close 等）、認證機制（Cognito JWT）、Request/Response 格式、分頁慣例、錯誤代碼（400/401/403/404/409/429/500）與 enum 定義 |
| `llm_tools.md` | **對話大腦工具規格**：定義 AgentCore Runtime 自動調用的 13 個工具，分四大類——行程管理類（get_today_routines / remind_pending_routines / complete_routine / create_routine / update_routine / deactivate_routine）、事件與摘要類（get_elder_profile / update_elder_profile / get_recent_events）、安全與警報類（notify_caregiver）、衛教知識類（search_health_knowledge）。含觸發意圖、安全冷卻機制與 elder_id 注入說明 |

### 功能設計文件

| 文件 | 說明 |
|------|------|
| `feature_events-extraction.md` | **生活記錄事件萃取計畫**：說明如何從自然對話萃取結構化事件（diet / activity / sleep / medication / wellbeing / safety / other）。涵蓋 Session batch 觸發條件、萃取 Pipeline 各階段設計、canonical key 身分建構、slot 去重與冪等寫入 |
| `feature_segmenter-pairwise-v2.md` | **對話分塊器 Pairwise V2**：基於 embedding 餘弦相似度的對話主題切割演算法，含訓練資料格式、標註規範、模型訓練流程與 F1/Precision/Recall 評估指標 |
| `feature_daily-summarization.md` | **每日摘要排程機制**：nightly 深夜生成當日摘要、data_status（partial / complete）二態設計、backfill 等待視窗重算邏輯、覆寫優先序（complete 不被 partial 覆蓋）、LLM 生成文字 vs 程式計算事實的分工原則 |

### 開發規範與指南

| 文件 | 說明 |
|------|------|
| `workflow.md` | **開發流程**：Git 分支策略（main 為主分支、feature/ 與 fix/ 開分支）、Conventional Commits 格式（feat / fix / docs / refactor / test / chore）、PR 規範 |
| `conventions.md` | **開發慣例**：命名規則（snake_case 檔名、lowerCamelCase 變數、PascalCase 類別）、註解語言（繁體中文）、測試要求與 Linter 規範 |
| `local_testing.md` | **本機測試指南**：如何在本地環境建立 DynamoDB Local、執行 Backend pytest 與 Extraction Pipeline 單元測試 |
| `pii.md` | **個資處理政策**：系統中個人識別資訊（PII）的處理規範、資料遮蔽策略與保護措施 |
| `user-journey.md` | **使用者旅程**：長者與照護者的完整操作流程劇本，從註冊、首次設定、日常對話到照護者查看摘要的端到端情境描述 |
