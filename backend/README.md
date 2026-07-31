# backend/ — Python Lambda

每個 handler 對應一組 API 資源，由 API Gateway（Cognito JWT authorizer）觸發；`summary_generator` 由 EventBridge Scheduler 每晚觸發。API 規格見 [docs/api.md](../docs/api.md)，ASR 子系統架構見 [docs/asr/framework.md](../docs/asr/framework.md)。

```
src/
├── handlers/     # chat / elders / summaries / events / routines / stats / summary_generator / pre_token_generation（Cognito trigger）
├── extraction/   # 生活記錄（Module B）萃取 pipeline：分類體系、分塊、分類、萃取、canonical identity、去重
│   └── assets/   # 隨部署包發佈的資產：taxonomy/ 分類體系、retrieval/ 概念檢索 sub-chunks
└── shared/       # auth（token 授權）、db（DynamoDB 六表）、models（Pydantic schema）、
                  # responses（統一回應格式）、routines（例行公事版本與 occurrence 推導）、
                  # bedrock（模型呼叫）、sessions（session 生命週期）、
                  # turns（turn 請求狀態機與冪等）、metrics（觀測指標）、
                  # asr（remote-only 語音辨識領域模組）
scripts/          # 離線工具（資產檢視、索引建置、模型導出、分塊模型工作流）
training/         # 分塊模型 pairwise_v2 的離線訓練與評測（語料、特徵、指標）
tests/            # pytest
```

離線工具以 module 形式執行，例如 `python -m scripts.dump_taxonomy` 印出節點與高階類別對照表。

`training/` 與 `scripts/segmenter_v2_*.py` 是離線工作流，不進 Lambda 部署包（`pyproject.toml` 的 packages 只收 `src*`）；操作步驟見 [docs/feature_segmenter-pairwise-v2.md](../docs/feature_segmenter-pairwise-v2.md)。

`extraction/` 只由 batch 相關 Lambda 使用，不進 realtime `/chat` 路徑；設計與移植步驟見 [docs/feature_events-extraction.md](../docs/feature_events-extraction.md)。

## 開發

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

依賴管理在 `pyproject.toml` 與 `requirements.txt`：`requirements.txt` 為 Lambda 執行期依賴（由 Terraform 社群模組 `terraform-aws-modules/lambda/aws` 的 `pip_requirements` 自動安裝打包），`[dev]` 為開發期工具，`[training]` 為離線訓練工作流所需（scikit-learn、numpy）。handlers 以 `from src.shared import ...` 匯入，editable install 後即可解析。

## RAG PoC（衛教知識庫問答）

已移至 [experiments/rag-poc/](../experiments/rag-poc/)——與此 Lambda 骨架分開維護（在本機用 Chroma 跑通檢索；生成層待接 Bedrock）。正式版由 `chat.py` 接 Bedrock Knowledge Base 實作。
