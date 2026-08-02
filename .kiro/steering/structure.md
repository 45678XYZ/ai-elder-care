---
inclusion: always
---

# 專案結構與文件索引

## 目錄地圖

```text
├── app/              # Flutter：elder/ caregiver/ 兩組頁面 + shared/ 服務層
├── backend/          # Python Lambda handlers、對話大腦、ASR/TTS 領域模組、萃取 pipeline
├── terraform/        # AWS IaC（20 個 .tf，依資源領域分檔）
├── asr-container/    # ASR 推論容器原始碼（FormoSpeech 客語六腔）
├── tts-container/    # TTS 推論容器原始碼（BreezyVoice 華語、OmniVoice 客語）
├── data/             # 模擬 persona、合成情境腳本、seed 腳本、knowledge/ 衛教文件
├── docs/             # 架構、API、旅程、PII、慣例、交付文件
│   └── features/     # 功能與子系統規格：asr/ tts/ adr/、摘要、模型選型、整併計畫
├── scripts/          # 全域工具腳本（知識庫上傳與同步）
└── .kiro/            # steering、specs 與 repo skill（skills/ 已移入 .kiro/skills/）
```

## backend/src/ 四層

依賴方向是單向的：`handlers/` → `shared/`／`extraction/`。反向依賴（`shared/` 匯入 `handlers/`）是設計錯誤。

| 層 | 內容 | 職責邊界 |
|---|---|---|
| `handlers/` | 14 支 Lambda 入口：`chat` `tools` `elders` `routines` `events` `summaries` `stats` `session_closer` `batch_extractor` `dlq_reconciler` `daily_digest` `summary_generator` `pre_token_generation` `post_confirmation` | **只做入口與 I/O**：解析事件、授權、呼叫共用層、組回應。業務邏輯不留在這裡 |
| `shared/` | `auth` `db` `models` `responses` `routines` `sessions` `turns` `bedrock` `metrics` `summarizer` `validation` `config_source` `asr_http` + `asr/` `tts/` 兩個子套件 | 跨 handler 與 Runtime 共用的基礎設施層。DynamoDB 存取一律走 `db`，回應一律走 `responses` |
| `extraction/` | `pipeline`（含 `ExtractionConfig` 與 `DirectSevenPipeline`）`canonical` `dedup` `temporal` `taxonomy` `models` + `assets/taxonomy/` | 只給 batch 相關 Lambda，**不進 realtime `/chat` 路徑**。純記憶體運算，不自己寫 DynamoDB（寫入由 `batch_extractor` 負責） |
| `agentcore_runtime/` | `graph` `runtime` `prompts` `tools` `config` `main` | 對話大腦。**唯一不跑在 Lambda 上的部分**，以 zip 部署到 Bedrock AgentCore Runtime |

萃取 pipeline 目前只有 `direct_seven`：**不分塊、不檢索、不做 RAC 分類**，依 turn 邊界按字元上限分批，每批一次 LLM 呼叫萃取七大類，再經 SharedTail 做時間解析、canonical key、slot 去重與型別驗證。

對話工具共 13 個：12 個邏輯在 `handlers/tools.py`（AgentCore Runtime 透過 `lambda:InvokeFunction` 呼叫），`search_health_knowledge` 在 Runtime 內直接呼叫 Bedrock Knowledge Base。

## 常見改動要動哪裡

- **新增 API endpoint**：`docs/api.md`（先定契約）→ `backend/src/handlers/<資源>.py` → `terraform/api_gateway.tf` 與 `terraform/lambda.tf`（路由與權限）→ `backend/tests/` → App 端 `api_repository.dart`。
- **新增對話工具**：`docs/llm_tools.md` → `handlers/tools.py` 的分派 → `agentcore_runtime/tools.py` 的 LangChain 包裝。
- **改事件類別**：`extraction/assets/taxonomy/high_level_types.json` → summary `sections` → `docs/api.md` → `shared/summarizer.py`。

## 命名慣例

Python `snake_case`／`PascalCase`／`UPPER_SNAKE_CASE`；Dart `lowerCamelCase`／`PascalCase`（常數也用 `lowerCamelCase`）；檔名與 Terraform resource 一律 `snake_case`。API 欄位 `snake_case`、ID 帶型別前綴。

完整規則見 [docs/conventions.md](../../docs/conventions.md)——實作時照著用，不自創。

## 權威文件索引

先讀 [docs/framework.md](../../docs/framework.md)。要動某個主題前先讀對應文件，不要憑印象改：

| 主題 | 文件 |
|---|---|
| 架構、DynamoDB 資料模型與寫入規則、Session 狀態機、後端環境變數 | [docs/framework.md](../../docs/framework.md) |
| 上述的易讀導覽版 | [docs/framework-overview.md](../../docs/framework-overview.md) |
| API 端點、request/response、錯誤格式（前後端唯一契約） | [docs/api.md](../../docs/api.md) |
| 上述的易讀導覽版 | [docs/api-overview.md](../../docs/api-overview.md) |
| 對話大腦 13 個工具的觸發條件與 I/O | [docs/llm_tools.md](../../docs/llm_tools.md) |
| 每日摘要排程、partial/complete、backfill | [docs/features/feature_daily-summarization.md](../../docs/features/feature_daily-summarization.md) |
| 命名、註解、風格與提交前檢查 | [docs/conventions.md](../../docs/conventions.md) |
| 分支、Commit、PR | [docs/workflow.md](../../docs/workflow.md) |
| PII 與安全政策 | [docs/pii.md](../../docs/pii.md) |
| 使用者旅程 | [docs/user-journey.md](../../docs/user-journey.md) |
| 本機不連 AWS 的測試路徑 | [docs/local_testing.md](../../docs/local_testing.md) |
| ASR 子系統架構、設定 schema、模型目錄、推論契約、安全 | [docs/features/asr/](../../docs/features/asr/framework.md) |
| TTS 子系統同上 | [docs/features/tts/](../../docs/features/tts/framework.md) |
| remote-only、Transcribe 路由、模型核准等決策紀錄 | [docs/features/adr/](../../docs/features/adr/asr-remote-only.md) |
| ASR／TTS 模型選型比較 | [docs/features/model_selection_asr_tts.md](../../docs/features/model_selection_asr_tts.md) |
| 交付版旅程、資料應用、前端資料流 | [docs/deliverables/](../../docs/deliverables/user-journey.md) |
| App → 後端待辦需求（腔調／語言改由工具寫入） | [docs/features/request_elder-lang-dialect-via-tool.md](../../docs/features/request_elder-lang-dialect-via-tool.md) |
| ASR／Agent／前端相容性整併計畫 | [docs/features/asr-agentcore-frontend-integration-plan.md](../../docs/features/asr-agentcore-frontend-integration-plan.md) |

逐檔說明看各目錄自己的 README：[app/README.md](../../app/README.md)、[backend/README.md](../../backend/README.md)、[backend/src/README.md](../../backend/src/README.md)、[backend/src/shared/asr/README.md](../../backend/src/shared/asr/README.md)、[backend/src/shared/tts/README.md](../../backend/src/shared/tts/README.md)、[data/README.md](../../data/README.md)、[docs/README.md](../../docs/README.md)、[asr-container/README.md](../../asr-container/README.md)、[tts-container/README.md](../../tts-container/README.md)。App 設計規範見 [app/design-system/MASTER.md](../../app/design-system/MASTER.md)。

改文件或搬動頂層檔案／目錄時，順手更新引用它的地方（README 結構樹、文件清單、交叉連結），別讓索引長出死連結。
