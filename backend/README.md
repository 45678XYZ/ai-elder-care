# backend/ — Python Lambda

每個 handler 對應一組 API 資源，由 API Gateway（Cognito JWT authorizer）觸發；`summary_generator` 由 EventBridge Scheduler 每晚觸發。API 規格見 [docs/api.md](../docs/api.md)。

```
src/
├── handlers/     # chat / elders / summaries / events / routines / stats / summary_generator
└── shared/       # auth（token 授權）、db（DynamoDB 六表）、responses（統一回應格式）
tests/            # pytest
```

## 開發

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

依賴管理在 `pyproject.toml`：`dependencies` 為執行期（進 Lambda 部署包），`[dev]` 為開發期工具。handlers 以 `from src.shared import ...` 匯入，editable install 後即可解析。

## RAG PoC（衛教知識庫問答）

跟上面的 Lambda 骨架分開，驗證長照衛教問答的檢索/生成邏輯，之後 `chat.py` 要接 Bedrock 做 RAG 前先在本機用 Chroma + Gemini 跑通。依賴列在 `requirements.txt`（目前裝在系統 Python，不在 `.venv` 裡，兩邊尚未整合）。

```
kb/            # 知識庫來源 txt，授權/收錄範圍說明見 kb/README.md
embedding.py   # 向量化（多語 embedding model）
bm25_search.py # BM25 關鍵字檢索
reranker.py    # cross-encoder 重排
ingest.py      # 建立/重建 Chroma 向量庫：python ingest.py
rag.py         # 檢索 + Gemini 生成核心邏輯
query.py       # CLI 測試：python query.py "問題"
server.py      # FastAPI /ask：uvicorn server:app
```

執行前需要 `backend/.env`（參考 `.env.example`）設定 `GEMINI_API_KEY`。
