# backend/src/ — 後端核心原始碼

本目錄為系統後端的核心邏輯，包含 API Handler、對話大腦、事件萃取 Pipeline 與共用模組。所有程式碼以 Python 撰寫，部署至 AWS Lambda 與 Bedrock AgentCore Runtime。

## 目錄結構

```
src/
├── handlers/             # AWS Lambda Handler（API 與排程／佇列進入點）
├── agentcore_runtime/    # 對話大腦（LangGraph 狀態機），部署到 AgentCore Runtime 而非 Lambda
├── extraction/           # 生活記錄萃取 Pipeline（direct_seven），只給 batch 用
├── shared/               # 共用模組（DB、Auth、Models、Bedrock、ASR、TTS）
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

目前只有一條 pipeline：`direct_seven`——**不分塊、不檢索、不做 RAC 分類**。整個 session 的 frozen turns 依 `SEVEN_BATCH_CHAR_LIMIT` 在 turn 邊界貪婪分批，每批一次 LLM 呼叫萃取七大類事件，再交給共用尾段（SharedTail）收斂。

| 檔案 | 功能 |
|------|------|
| `pipeline.py` | 本模組的主體，含四部分：`ExtractionConfig`（萃取設定與 `from_env()`）、七大類萃取的 prompt 與 schema 驗證／修復、`_SharedTail`（時序解析 → canonical key → slot 去重 → 型別驗證）、`DirectSevenPipeline` 編排器與 `plan_turn_batches` 分批 |
| `temporal.py` | 時序解析與正規化：相對時間表達推導、台灣時區轉換、`observed_at` 絕對化 |
| `canonical.py` | Canonical 身分建構：正規化 subject/predicate、建構 `canonical_event_key` 與 `event_id`，確保同一事件重跑產生相同 ID |
| `dedup.py` | Slot 去重：同 subject + predicate 且落在同一時間桶的事件收斂為一筆，可選用 embedding 做謂語語義合併 |
| `taxonomy.py` | 分類體系管理：載入與查詢 `assets/taxonomy/` 下的高階類別與謂語辭典 |
| `models.py` | 萃取過程使用的資料模型（`Turn`／`ExtractedEvent`／`CanonicalEvent`／`DedupStats`） |

### extraction/assets/

| 子目錄 | 說明 |
|--------|------|
| `taxonomy/` | 分類體系靜態資料：`high_level_types.json`（七大高階類別定義）、`predicate_lexicon.json`（謂語辭典與 alias） |

---

## shared/ — 共用模組

跨 Handler 與 Runtime 共用的基礎設施層。

| 檔案 | 功能 |
|------|------|
| `db.py` | DynamoDB 統一存取層：提供 7 張表（elders / conversations / events / daily_summaries / routines / caregiver-lookup / elder_accounts）的讀寫介面。含條件式寫入、canonical event 冪等、Base64 分頁游標、Decimal 自動轉碼 |
| `auth.py` | 授權模組：從 Cognito JWT claims 取得呼叫者身分，驗證長者/照護者存取權限。長者只能存取自己，照護者只能存取綁定的長者 |
| `models.py` | Pydantic 資料模型：定義所有 Request/Response schema（ChatRequest、ElderCreate、RoutineDefinition、EventCreate、DailySummaryCreate 等） |
| `bedrock.py` | Bedrock 呼叫層：Converse API、structured outputs（含降級路徑）、embedding。錯誤分成 retryable 與 permanent 供 batch worker 決策 |
| `sessions.py` | Session 管理：Session 狀態機操作（建立、inflight 名額、close、batch 狀態推進）與 lease 租約機制 |
| `turns.py` | Turn 管理：對話輪次的生命週期（processing → completed / failed）、冪等判定與租約接管 |
| `routines.py` | Routine 推導邏輯：occurrence 狀態不落地，由 canonical completion event 與寬限期即時推導（pending / done / missed） |
| `summarizer.py` | 摘要生成共用邏輯：可計算事實由程式算（interaction_count、routine 統計）、自然語言由 Bedrock 模型寫。`POST /summaries/generate` 與排程 generator 共用 |
| `metrics.py` | CloudWatch 指標發送：萃取/摘要/batch 各階段的 EMF 格式觀測指標 |
| `responses.py` | API 回應格式工具：統一 200/4xx/5xx 的 response 結構與 error code 生成 |
| `validation.py` | 請求驗證工具：參數校驗與格式檢查的通用 helper |
| `config_source.py` | ASR／TTS 設定的解析與來源切換：設定 JSON 太大放不進 Lambda 環境變數（4 KB 上限）時改由 SSM Parameter Store 讀取。讀取失敗一律 fail closed |
| `asr_http.py` | ASR 錯誤到 HTTP 回應的映射：把 provider 例外收斂成 `docs/api.md` 已定義的錯誤碼，且不讓內部細節外洩到回應訊息 |
| `asr/` | Remote-only 語音辨識子套件：`canonical_audio`（音訊正規化）、`router`／`facade`（路由與備援鏈）、`providers`（Transcribe／SageMaker）、`config`／`composition`（設定與組裝）、`telemetry`、`types`。詳見 [shared/asr/README.md](shared/asr/README.md) |
| `tts/` | 可切換華語／客語的遠端語音合成子套件：`router`（語言與六腔路由、同語言備援）、`providers`、`config`／`composition`、`types`。詳見 [shared/tts/README.md](shared/tts/README.md) |
