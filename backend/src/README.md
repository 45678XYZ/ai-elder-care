# backend/src/ — 後端核心原始碼

本目錄為系統後端的核心邏輯，包含 API Handler、對話大腦、事件萃取 Pipeline 與共用模組。所有程式碼以 Python 撰寫，部署至 AWS Lambda 與 Bedrock AgentCore Runtime。

## 目錄結構

```
src/
├── handlers/             # AWS Lambda Handler（API 進入點）
├── agentcore_runtime/    # 對話大腦（LangGraph 狀態機）
├── extraction/           # 生活記錄萃取 Pipeline
├── shared/               # 共用模組（DB、Auth、Models、Bedrock）
└── __init__.py
```

---

## handlers/ — AWS Lambda Handlers

所有 API Gateway 路由的進入點，以及排程觸發的背景工作。

| 檔案 | 功能 |
|------|------|
| `chat.py` | `POST /chat` 對話核心：解析請求 → Cognito 授權 → 冪等判定 → Session 選擇/建立 → ASR 語音轉文字 → AgentCore Runtime 推理 → TTS 語音合成 → 提交結果。支援中文（Polly）與客語（OmniVoice）|
| `elders.py` | `GET/POST/PATCH /elders` 長者資料 CRUD：建立長者（自動綁定照護者）、查詢長者資訊、更新健康注意事項與暱稱 |
| `events.py` | `GET /events` 生活事件時間軸：依日期範圍與事件類型查詢結構化生活事件，倒序分頁回傳 |
| `routines.py` | `GET/POST/PATCH /routines` 與 `POST /routines/{id}/complete` 例行公事管理：定義列表、當日行程視圖、建立/更新/停用行程、手動確認完成 |
| `stats.py` | `GET /stats` 互動與行程統計：計算期間內對話輪數、逐日趨勢、routine 完成率 |
| `summaries.py` | `GET /summaries` 與 `POST /summaries/generate` 每日摘要：列表查詢與手動觸發生成 |
| `summary_generator.py` | EventBridge 排程每日摘要生成：nightly 模式（深夜生成當日摘要）與 backfill 模式（重算 partial 摘要） |
| `daily_digest.py` | 每日晚報（22:00 台灣時間）：掃描所有長者，組裝今日健康摘要與行程完成狀況，透過 SNS 推播至照護者信箱 |
| `session_closer.py` | Session 關閉器：前端主動 close API + EventBridge 週期性 idle 收斂，觸發離線 batch materialization |
| `batch_extractor.py` | SQS 批次事件萃取器：從佇列接收 closed session，執行 Extraction Pipeline，條件式寫入 events。實現 At-Least-Once → Exactly-Once 語義 |
| `tts_worker.py` | 非同步 TTS 合成 worker：從 SQS 取合成工作，呼叫已核准的 TTS provider，寫入 S3 後把 object key 補回 turn |
| `dlq_reconciler.py` | SQS DLQ 調和器：處理重試耗盡的 batch 訊息，標記 failed 並發送 SNS 告警 |
| `tools.py` | 對話大腦工具箱 Lambda：AgentCore Runtime 透過 `lambda:InvokeFunction` 呼叫，分派 12 個業務工具（行程管理、事件查詢、安全通知）。含緊急通知 5 分鐘冷卻機制 |
| `post_confirmation.py` | Cognito Post Confirmation Trigger：照護者完成註冊後自動訂閱 SNS Topic（緊急警報 + 晚報） |
| `pre_token_generation.py` | Cognito Pre Token Generation Trigger：發 ID token 前查詢 elder_accounts 表，為長者帳號注入 `elder_id` claim |

---

## agentcore_runtime/ — 對話大腦

基於 LangGraph 狀態機的對話 AI Runtime，取代 Bedrock Classic Agent，提供更高的可控性與工具管理能力。

| 檔案 | 功能 |
|------|------|
| `graph.py` | LangGraph 狀態機定義：`agent` 節點（思考 + 決定是否呼叫工具）與 `tools` 節點（執行工具），循環直到模型不再要求工具。記錄 `tools_called` 供 chat Lambda 換算 `routines_updated` 與 `safety_alert_triggered` |
| `runtime.py` | Runtime 執行入口：接收 chat Lambda 傳入的 payload，初始化 graph 並 invoke，回傳最終回覆文字與工具呼叫紀錄 |
| `prompts.py` | System Prompt 定義：對話大腦的人格設定、回應規範與工具使用指引 |
| `tools.py` | 13 個工具的 LangChain 包裝：12 個業務工具透過 `lambda:InvokeFunction` 轉呼叫 Tools Lambda，第 13 個 `search_health_knowledge` 直接呼叫 Bedrock Knowledge Base Retrieve |
| `config.py` | 環境變數與設定（AWS Region、Lambda ARN、Knowledge Base ID、模型 ID 等） |
| `main.py` | AgentCore Runtime 容器進入點 |

---

## extraction/ — 生活記錄萃取 Pipeline

端到端事件萃取模組，從 frozen turns 萃取結構化生活事件。純記憶體運算，不直接寫入 DynamoDB（DB 寫入由 batch_extractor.py 負責）。

| 檔案 | 功能 |
|------|------|
| `pipeline.py` | 編排器：串接所有萃取階段（chunk → retrieve → classify → prune → compose → extract → temporal → canonical → dedup） |
| `chunker.py` | 對話分塊器：將長對話依主題切割成獨立 chunk |
| `chunk_planner.py` | Chunk 計畫器：決定分塊策略、生成 ChunkManifest（重試時重用確保冪等）、計算 reference datetime |
| `retriever.py` | 概念檢索：根據 chunk 內容從知識庫檢索相關概念，輔助後續分類 |
| `classifier.py` | RAC 分類器：判定每個 chunk 的事件類型（diet / activity / sleep / medication / wellbeing / safety / other） |
| `pruner.py` | HMLC 剪枝器：過濾低信心分類結果，保留高信心事件 |
| `schema_composer.py` | 動態 Schema 組裝：根據分類結果，為每個 chunk 組裝對應的萃取 schema |
| `extractor.py` | Single-Pass 萃取器：用 LLM 從 chunk 文字萃取結構化事件欄位 |
| `temporal.py` | 時序解析與正規化：日期時間解析、台灣時區轉換、day_key 計算 |
| `canonical.py` | Canonical 身分建構：正規化 subject/predicate、建構 canonical_event_key，確保同一事件重跑產生相同 ID |
| `dedup.py` | Slot 去重：相同 canonical key 的事件僅保留信心值最高者 |
| `segmenter.py` | Pairwise V2 分塊模型推理（基於 embedding 相似度的主題邊界偵測） |
| `taxonomy.py` | 分類體系管理：載入與查詢 `assets/taxonomy/` 下的本體論、概念映射與同義詞辭典 |
| `config.py` | 萃取 Pipeline 的環境設定與閾值參數 |
| `models.py` | 萃取過程使用的 Pydantic 資料模型 |

### extraction/assets/

| 子目錄 | 說明 |
|--------|------|
| `taxonomy/` | 分類體系靜態資料：`unified_care_ontology.json`（統一照護本體論）、`high_level_types.json`（高層分類）、`concept_type_map.json`（概念到類型映射）、`predicate_lexicon.json`（謂語辭典）、`property_registry.json`（屬性註冊表）、`synonym_dictionary.json`（同義詞辭典） |
| `segmenter/` | 分塊模型資料：`pairwise_v2.json`（Pairwise V2 模型參數） |
| `retrieval/` | 概念檢索資料：`concept_chunks.jsonl`（概念塊索引） |

---

## shared/ — 共用模組

跨 Handler 與 Runtime 共用的基礎設施層。

| 檔案 | 功能 |
|------|------|
| `db.py` | DynamoDB 統一存取層：提供 5 張表（elders / conversations / events / daily_summaries / routines）的讀寫介面。含條件式寫入、canonical event 冪等、Base64 分頁游標、Decimal 自動轉碼 |
| `auth.py` | 授權模組：從 Cognito JWT claims 取得呼叫者身分，驗證長者/照護者存取權限。長者只能存取自己，照護者只能存取綁定的長者 |
| `models.py` | Pydantic 資料模型：定義所有 Request/Response schema（ChatRequest、ElderCreate、RoutineDefinition、EventCreate、DailySummaryCreate 等） |
| `bedrock.py` | Bedrock 呼叫層：Converse API、structured outputs（含降級路徑）、embedding。錯誤分成 retryable 與 permanent 供 batch worker 決策 |
| `sessions.py` | Session 管理：Session 狀態機操作（建立、inflight 名額、close、batch 狀態推進）與 lease 租約機制 |
| `turns.py` | Turn 管理：對話輪次的生命週期（processing → completed / failed）、冪等判定與租約接管 |
| `routines.py` | Routine 推導邏輯：occurrence 狀態不落地，由 canonical completion event 與寬限期即時推導（pending / done / missed） |
| `summarizer.py` | 摘要生成共用邏輯：可計算事實由程式算（interaction_count、routine 統計）、自然語言由 Bedrock 模型寫。`POST /summaries/generate` 與排程 generator 共用 |
| `tts.py` | TTS 語音合成抽象層：統一介面支援 AWS Polly（中文）與 OmniVoice（客語），含 fallback 降級防護 |
| `metrics.py` | CloudWatch 指標發送：萃取/摘要/batch 各階段的 EMF 格式觀測指標 |
| `responses.py` | API 回應格式工具：統一 200/4xx/5xx 的 response 結構與 error code 生成 |
| `validation.py` | 請求驗證工具：參數校驗與格式檢查的通用 helper |
