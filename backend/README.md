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
│   ├── extraction/               # 生活記錄（Module B）萃取 pipeline：分類體系、分塊、分類、萃取、canonical identity、去重
│   │   └── assets/               # 隨部署包發佈的資產：taxonomy/ 分類體系、retrieval/ 概念檢索 sub-chunks
│   └── shared/                   # auth（token 授權）、db（DynamoDB 六表）、models（Pydantic schema）、
│                                 # responses（統一回應格式）、routines（例行公事版本與 occurrence 推導）、
│                                 # bedrock（模型呼叫）、sessions（session 生命週期）、
│                                 # turns（turn 請求狀態機與冪等）、metrics（觀測指標）、
│                                 # asr（Transcribe/SageMaker remote-only 語音辨識）、
│                                 # tts（可切換中文／客語遠端語音合成）
├── scripts/                      # 離線工具（資產檢視、索引建置、模型導出、分塊模型工作流）
├── training/                     # 分塊模型 pairwise_v2 的離線訓練與評測（語料、特徵、指標）
├── tests/                        # Pytest 單元測試與整合測試
├── pyproject.toml                # Python 專案配置（含 [dev] / [training] extras）
├── requirements.txt              # Lambda 執行期依賴（Terraform 打包用）
├── agentcore_requirements.txt    # 對話大腦專用依賴（langgraph / langchain_aws）
└── README.md
```

詳細的逐檔說明請見 **[src/README.md](src/README.md)**。

## scripts/ — 開發輔助腳本

| 檔案 | 功能 |
|------|------|
| `build_concept_vector_index.py` | 建構概念向量索引（供 Extraction Retriever 使用） |
| `draft_predicate_lexicon.py` | 草擬謂語辭典（canonical key 正規化用） |
| `dump_taxonomy.py` | 匯出/檢視分類體系（除錯用） |
| `resolve_event_identity.py` | 事件身分解析工具（驗證 canonical key 產生邏輯） |
| `segmenter_v2_prepare_corpora.py` | 準備 V2 分塊模型訓練語料 |
| `segmenter_v2_prepare_annotation.py` | 準備分塊標註資料 |
| `segmenter_v2_embed.py` | 產生分塊模型所需的 embedding |
| `segmenter_v2_train.py` | 訓練 Pairwise V2 分塊模型 |
| `segmenter_v2_evaluate.py` | 評估分塊模型效能（F1 / Precision / Recall） |
| `segmenter_v2_translate.py` | 翻譯分塊訓練資料 |
| `segmenter_v2_smoke.py` | 分塊模型冒煙測試 |

## training/segmenter_v2/ — 分塊模型訓練模組

| 檔案 | 功能 |
|------|------|
| `contract.py` | 訓練/推理介面契約定義 |
| `corpora.py` | 語料載入與預處理 |
| `embeddings.py` | Embedding 特徵萃取 |
| `baselines.py` | 基線模型（用於效能比較） |
| `metrics.py` | 評估指標計算（F1 / Precision / Recall） |
| `export.py` | 模型導出（產生 `assets/segmenter/pairwise_v2.json`） |
| `paths.py` | 訓練資料路徑管理 |

## tests/ — 測試

涵蓋所有核心模組的單元測試與整合測試，基於 pytest：

| 測試檔案 | 測試範圍 |
|----------|----------|
| `test_chat.py` | POST /chat 完整流程 |
| `test_elders.py` | 長者 CRUD API |
| `test_routines_api.py` / `test_routines_domain.py` | 行程 API + 推導邏輯 |
| `test_events_handler.py` / `test_events_data_layer.py` | 事件 API + 資料層 |
| `test_summaries_handler.py` / `test_summaries_end_to_end.py` | 摘要 API + E2E |
| `test_stats_handler.py` | 統計 API |
| `test_session_closer.py` / `test_sessions.py` | Session 關閉與管理 |
| `test_batch_extractor.py` | 批次萃取器 |
| `test_extraction_*.py` | Extraction Pipeline 各階段（pipeline / chunking / classifier / pruner / retriever / schema_composer / segmenter / taxonomy / temporal / canonical / extractor） |
| `test_summarizer.py` / `test_summary_generator.py` | 摘要生成邏輯 |
| `test_auth.py` | 認證與授權 |
| `test_db.py` | DynamoDB 存取層 |
| `test_bedrock_client.py` | Bedrock 呼叫層 |
| `test_tools.py` | Tools Lambda 工具箱 |
| `test_models.py` | Pydantic 模型驗證 |
| `test_metrics.py` | CloudWatch 指標 |
| `test_turns.py` | Turn 生命週期 |

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

若需訓練/評估分塊模型：
```bash
pip install -e ".[training]"
```
