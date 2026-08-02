# backend/ — 雲端後端模組

本模組為系統的核心大腦與資料處理中樞，採用 **Python** 實作，以無伺服器 (Serverless) 架構部署至 AWS Lambda 與 Bedrock AgentCore Runtime。

每個 handler 對應一組 API 資源，由 API Gateway（Cognito JWT authorizer）觸發；`summary_generator` 由 EventBridge Scheduler 每晚觸發。API 規格見 [docs/api.md](../docs/api.md)，語音子系統架構見 [ASR](../docs/asr/framework.md) 與 [TTS](../docs/tts/framework.md)。

## 模組功能

1. **對話大腦 (AgentCore Runtime)** — 基於 LangGraph 狀態機的對話 AI，取代 Bedrock Classic Agent，提供長期記憶、tool calling 與高可控性推理
2. **RESTful API 服務 (Handlers)** — 提供 App 端所需的全部 API（對話、長者資料、行程、事件、摘要、統計）
3. **生活記錄萃取 (Extraction Pipeline)** — 從自然對話中自動萃取結構化生活事件（飲食/活動/睡眠/用藥/安全等）
4. **排程任務** — EventBridge 驅動的每日摘要生成、晚報推播、idle session 收斂

## 目錄結構

```
backend/
├── src/
│   ├── handlers/                 # AWS Lambda Handlers（14 個 API/背景工作進入點）
│   ├── agentcore_runtime/        # 對話大腦：LangGraph 狀態機、工具包裝、人設；部署到 AgentCore Runtime 而非 Lambda
│   ├── extraction/               # 生活記錄（Module B）萃取 pipeline：direct_seven 七大類萃取、
│   │   │                         # canonical identity、slot 去重、時序解析、分類體系
│   │   └── assets/               # 隨部署包發佈的資產：taxonomy/ 高階類別與謂語辭典
│   └── shared/                   # auth（token 授權）、db（DynamoDB 七表）、models（Pydantic schema）、
│                                 # responses（統一回應格式）、routines（例行公事版本與 occurrence 推導）、
│                                 # bedrock（模型呼叫）、sessions（session 生命週期）、
│                                 # turns（turn 請求狀態機與冪等）、metrics（觀測指標）、
│                                 # summarizer（摘要生成）、validation（請求校驗）、
│                                 # config_source（ASR/TTS 設定來源，過大時走 SSM）、
│                                 # asr（Transcribe/SageMaker remote-only 語音辨識）、
│                                 # tts（可切換華語／客語遠端語音合成）
├── local_runner/                 # 不連 AWS 的本機萃取／對話試跑腳本（見 local_runner/README.md）
├── scripts/                      # 離線工具（分類體系檢視、謂語辭典草擬、事件身分驗證）
├── tests/                        # Pytest 單元測試與整合測試
├── pyproject.toml                # Python 專案配置（含 [dev] extras）
├── requirements.txt              # Lambda 執行期依賴（Terraform 打包用）
├── agentcore_requirements.txt    # 對話大腦專用依賴（langgraph / langchain_aws）
└── README.md
```

詳細的逐檔說明請見 **[src/README.md](src/README.md)**。

## scripts/ — 開發輔助腳本

| 檔案 | 功能 |
|------|------|
| `draft_predicate_lexicon.py` | 草擬謂語辭典（canonical key 正規化用） |
| `dump_taxonomy.py` | 匯出/檢視分類體系（除錯用） |
| `resolve_event_identity.py` | 事件身分解析工具（驗證 canonical key 產生邏輯） |

## tests/ — 測試

涵蓋所有核心模組的單元測試與整合測試，基於 pytest。`tests/` 根目錄為 API、資料層與萃取測試，`tests/asr/`、`tests/tts/` 為語音子系統測試：

| 測試檔案 | 測試範圍 |
|----------|----------|
| `test_chat.py` | POST /chat 完整流程 |
| `test_elders.py` | 長者 CRUD API |
| `test_routines_api.py` / `test_routines_domain.py` / `test_routine_occurrences.py` | 行程 API、推導邏輯與 occurrence 收斂 |
| `test_events_handler.py` / `test_events_data_layer.py` | 事件 API + 資料層 |
| `test_summaries_handler.py` / `test_summaries_data_layer.py` / `test_summaries_end_to_end.py` | 摘要 API、資料層 + E2E |
| `test_stats_handler.py` | 統計 API |
| `test_sessions.py` / `test_session_closer.py` / `test_session_pending.py` | Session 生命週期、關閉收斂與 pending 判定 |
| `test_conversations_data_layer.py` / `test_get_recent_conversations.py` | 對話資料層與近期 context 取用 |
| `test_batch_extractor.py` | 批次萃取器（含條件式寫入與 lease） |
| `test_extraction_config.py` / `test_extraction_canonical.py` / `test_extraction_taxonomy.py` / `test_extraction_temporal.py` | 萃取設定、canonical identity、分類體系、時序解析 |
| `test_module_b_end_to_end.py` | Module B 從 session close 到事件落地的端到端 |
| `test_summarizer.py` / `test_summary_generator.py` | 摘要生成邏輯與排程 generator |
| `test_auth.py` | 認證與授權 |
| `test_post_confirmation.py` / `test_pre_token_generation.py` | Cognito 觸發器 |
| `test_db.py` | DynamoDB 存取層 |
| `test_bedrock_client.py` | Bedrock 呼叫層 |
| `test_config_source.py` | ASR/TTS 設定來源（環境變數 vs SSM、fail closed） |
| `test_tools.py` | Tools Lambda 工具箱 |
| `test_models.py` | Pydantic 模型驗證 |
| `test_metrics.py` | CloudWatch 指標 |
| `test_turns.py` | Turn 生命週期 |
| `tests/asr/` | ASR canonical audio、路由與備援、provider、telemetry、chat bridge、terraform 設定契約 |
| `tests/tts/` | TTS 合成路由與 terraform 設定契約 |

## 開發指南

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

若需測試 AgentCore Runtime：
```bash
pip install -r agentcore_requirements.txt
```
