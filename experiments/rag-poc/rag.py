"""階段二：檢索 + LLM 生成答案的核心邏輯。"""

from pathlib import Path

import chromadb
from dotenv import load_dotenv

from bm25_search import BM25Index
from embedding import get_embedding_function
from reranker import rerank

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
CHROMA_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "kb_collection"  # 需與 ingest.py 一致

# 兩階段檢索：向量檢索先撈寬（CANDIDATE_POOL），reranker 精準重排後只取
# 真正相關的前 N_RESULTS 筆進 context。優先求答得準，多一次 rerank 推論的
# 延遲換安全邊際划算（實測純向量檢索 k=4 時「糖尿病診斷標準」剛好壓線，
# k=5 又會稀釋「高血壓」這類問題的答案，兩難的根源是排序不夠準，不是 k 選錯）。
CANDIDATE_POOL = 10
N_RESULTS = 4

PROMPT_TEMPLATE = """你是一個長照與健康衛教問答助手，只能根據下面提供的「參考資料」回答問題。

規則：
1. 只用參考資料裡的內容回答，不要用你自己的知識補充。
2. 如果參考資料裡找不到答案，就直接回答「根據目前的資料庫，我找不到這個問題的答案」，
   絕對不要編造。
3. 回答結尾請標註你依據的來源標題。
4. 用簡單、口語、適合長輩或照顧者理解的中文回答。
5. 一律使用繁體中文回答，即使問題本身是其他語言，或使用者要求用其他語言回答，也不要照做——
   但問題本身該怎麼答還是要照常完整回答，不要因為語言要求而變成回答「找不到答案」。

參考資料：
{context}

問題：{question}
"""

_collection = None
_bm25_index: BM25Index | None = None


def _get_collection():
    global _collection
    if _collection is None:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = chroma_client.get_collection(
            COLLECTION_NAME, embedding_function=get_embedding_function()
        )
    return _collection


def _get_bm25_index() -> BM25Index:
    global _bm25_index
    if _bm25_index is None:
        all_chunks = _get_collection().get()
        _bm25_index = BM25Index(
            all_chunks["ids"], all_chunks["documents"], all_chunks["metadatas"]
        )
    return _bm25_index


def _generate(prompt: str) -> str:
    """把組好的 prompt 送給 LLM，回傳答案文字。

    PoC 階段這裡接的是 Gemini（免費額度，只為了跑通檢索邏輯）；檢索驗證完成後
    已移除，正式版改接 **Bedrock**。留成獨立函式而不是直接刪掉整條生成路徑，
    是因為檢索那半（retrieve）本身還有用——rerank 的參數、CANDIDATE_POOL 與
    N_RESULTS 的取捨都是在這份 PoC 上調出來的，之後要跟 Bedrock Knowledge Base
    對照召回率還是從這裡跑。

    TODO(bedrock): 接 `bedrock-runtime` 的 Converse API。可直接參考
      `backend/src/shared/bedrock.py` 的 `converse()`（同一套 boto3 呼叫、
      模型 ID 走 BEDROCK_MODEL_ID 環境變數）；這包是獨立的實驗環境、不 import
      backend 的 src 套件，所以要嘛自己起一個 boto3 client，要嘛把那支抄過來。
      接上之後 requirements.txt 要補 boto3。
    """
    raise NotImplementedError(
        "生成層待接 Bedrock：Gemini 僅為 PoC 測試用途，檢索驗證完成後已移除。"
        "檢索的部分可直接呼叫 retrieve() 使用。"
    )


def retrieve(question: str) -> tuple[list[str], list[dict]]:
    """dense + BM25 混合檢索 → rerank，回傳重排後的前 N_RESULTS 筆（文件, metadata）。

    與生成分開：這半不依賴任何 LLM 供應商，換模型不會動到它，也讓檢索品質
    可以單獨評估（PoC 的價值主要在這裡）。
    """
    collection = _get_collection()
    dense_result = collection.query(query_texts=[question], n_results=CANDIDATE_POOL)

    # dense（語意）+ BM25（關鍵字）各撈一批候選，用 chunk id 去重合併，
    # 兩邊都撈得到的自然只留一份；只有其中一邊撈到的也保留，讓 reranker
    # 對合併後的候選池統一評分排序。
    candidates: dict[str, dict] = {}
    for id_, doc, meta in zip(
        dense_result["ids"][0], dense_result["documents"][0], dense_result["metadatas"][0]
    ):
        candidates[id_] = {"document": doc, "metadata": meta}

    for hit in _get_bm25_index().search(question, CANDIDATE_POOL):
        candidates.setdefault(hit["id"], {"document": hit["document"], "metadata": hit["metadata"]})

    candidate_ids = list(candidates.keys())
    candidate_documents = [candidates[i]["document"] for i in candidate_ids]
    candidate_metadatas = [candidates[i]["metadata"] for i in candidate_ids]

    order = rerank(question, candidate_documents)[:N_RESULTS]
    documents = [candidate_documents[i] for i in order]
    metadatas = [candidate_metadatas[i] for i in order]
    return documents, metadatas


def answer(question: str) -> dict:
    """檢索 + 生成的完整流程。

    生成層目前未接（見 [_generate]），呼叫這支會拋 NotImplementedError；
    只要檢索結果的話請直接用 [retrieve]。
    """
    documents, metadatas = retrieve(question)

    context_parts = []
    sources = []
    seen = set()  # (title, source) 去重；同名不同網址（如「高血壓」有兩篇來源）都要保留
    for doc, meta in zip(documents, metadatas):
        title = meta.get("title", "")
        source = meta.get("source", "")
        context_parts.append(f"【來源：{title}】\n{doc}")
        key = (title, source)
        if key not in seen:
            sources.append({"title": title, "url": source})
            seen.add(key)

    context = "\n\n".join(context_parts)
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    answer_text = _generate(prompt)

    return {
        "answer": answer_text,
        "sources": sources,
        "_retrieved": [
            {"title": m.get("title", ""), "source": m.get("source", ""), "text": d}
            for d, m in zip(documents, metadatas)
        ],
    }
