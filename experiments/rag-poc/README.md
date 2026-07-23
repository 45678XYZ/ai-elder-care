# experiments/rag-poc/ — 衛教知識庫問答 PoC

長照衛教問答的檢索／生成 PoC,與 `backend/`(正式 Lambda)分開維護。用途:在本機用 Chroma + Gemini 跑通 RAG 的檢索與生成邏輯;驗證可行後,正式版由 `backend/src/handlers/chat.py` 改接 **Bedrock Knowledge Base** 實作(見 [docs/framework.md](../../docs/framework.md)、`terraform/bedrock_kb.tf`)。

> 這是丟棄式驗證。搬上 Bedrock 後,`embedding` / `reranker` / `bm25` / `chroma` 這套會由 Bedrock KB 內建取代;真正延續的是 `kb/` 的衛教文件與「照段落切塊」的做法。

## 檔案

```
kb/            # 知識庫來源 txt,授權/收錄範圍見 kb/README.md
embedding.py   # 多語 embedding（paraphrase-multilingual-MiniLM）
bm25_search.py # BM25 關鍵字檢索（補 dense embedding 漏接精確詞彙）
reranker.py    # cross-encoder 重排（bge-reranker-base）
ingest.py      # 建立/重建 Chroma 向量庫：python ingest.py
rag.py         # dense + BM25 檢索 → rerank → Gemini 生成
query.py       # CLI 測試：python query.py "問題"
server.py      # FastAPI /ask，供 App 呼叫：uvicorn server:app
```

檔案彼此用平鋪 import（`from rag import answer`），路徑錨在 `__file__`——一律**在本資料夾內執行**。

## 執行

```bash
cd experiments/rag-poc
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # 填入 GEMINI_API_KEY

python ingest.py                 # 首次：建向量庫（產生 chroma_db/）
uvicorn server:app --port 8000   # 起 /ask，供 App 連
```

App 端 `ApiConfig.baseUrl` 預設 `http://10.0.2.2:8000`(Android 模擬器連本機主機的 localhost)。
