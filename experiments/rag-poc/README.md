# experiments/rag-poc/ — 衛教知識庫問答 PoC

長照衛教問答的檢索／生成 PoC,與 `backend/`(正式 Lambda)分開維護。用途:在本機用 Chroma 跑通 RAG 的檢索邏輯;驗證可行後,正式版由 `backend/src/handlers/chat.py` 改接 **Bedrock Knowledge Base** 實作(見 [docs/framework.md](../../docs/framework.md)、`terraform/bedrock_kb.tf`)。

> **生成層目前未接。** PoC 期間為了跑通檢索,生成先接了 Gemini(免費額度);檢索驗證完成後已把該依賴整包移除,`rag.py` 的 `_generate()` 留成待接 Bedrock 的接縫。想看檢索結果請直接呼叫 `retrieve()`,`answer()` 與 `/ask` 在接上之前會拋 `NotImplementedError`。

> 這是丟棄式驗證。搬上 Bedrock 後,`embedding` / `reranker` / `bm25` / `chroma` 這套會由 Bedrock KB 內建取代;真正延續的是 `kb/` 的衛教文件與「照段落切塊」的做法。

## 檔案

```
kb/            # 知識庫來源 txt,授權/收錄範圍見 kb/README.md
embedding.py   # 多語 embedding（paraphrase-multilingual-MiniLM）
bm25_search.py # BM25 關鍵字檢索（補 dense embedding 漏接精確詞彙）
reranker.py    # cross-encoder 重排（bge-reranker-base）
ingest.py      # 建立/重建 Chroma 向量庫：python ingest.py
rag.py         # retrieve()：dense + BM25 檢索 → rerank；_generate()：待接 Bedrock
query.py       # CLI 測試：python query.py "問題"
server.py      # FastAPI /ask，供 App 呼叫：uvicorn server:app
```

檔案彼此用平鋪 import（`from rag import answer`），路徑錨在 `__file__`——一律**在本資料夾內執行**。

## 執行

```bash
cd experiments/rag-poc
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python ingest.py                 # 首次：建向量庫（產生 chroma_db/）
python query.py "高血壓要注意什麼"  # 檢索測試（生成未接，目前會拋 NotImplementedError）
```

生成層接上 Bedrock 之後才需要 `.env`(填 AWS 憑證/`BEDROCK_MODEL_ID`)與 `uvicorn server:app --port 8000`。

App 端 `ApiConfig.baseUrl` 預設 `http://10.0.2.2:8000`(Android 模擬器連本機主機的 localhost)。
