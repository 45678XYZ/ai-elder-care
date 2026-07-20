"""階段二：檢索 + LLM 生成答案的核心邏輯。"""

import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

from embedding import get_embedding_function

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
CHROMA_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "kb_collection"  # 需與 ingest.py 一致
N_RESULTS = 4

# 測試用 Gemini（免費額度），之後接正式 API 再換回 Anthropic
MODEL = os.environ.get("RAG_MODEL", "gemini-3.1-flash-lite")

PROMPT_TEMPLATE = """你是一個長照與健康衛教問答助手，只能根據下面提供的「參考資料」回答問題。

規則：
1. 只用參考資料裡的內容回答，不要用你自己的知識補充。
2. 如果參考資料裡找不到答案，就直接回答「根據目前的資料庫，我找不到這個問題的答案」，
   絕對不要編造。
3. 回答結尾請標註你依據的來源標題。
4. 用簡單、口語、適合長輩或照顧者理解的中文回答。

參考資料：
{context}

問題：{question}
"""

_client: genai.Client | None = None
_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = chroma_client.get_collection(
            COLLECTION_NAME, embedding_function=get_embedding_function()
        )
    return _collection


def _get_genai_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()  # 讀取環境變數 GEMINI_API_KEY
    return _client


def answer(question: str) -> dict:
    collection = _get_collection()
    result = collection.query(query_texts=[question], n_results=N_RESULTS)

    documents = result["documents"][0]
    metadatas = result["metadatas"][0]

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

    client = _get_genai_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=1024),
    )
    answer_text = response.text

    return {
        "answer": answer_text,
        "sources": sources,
        "_retrieved": [
            {"title": m.get("title", ""), "source": m.get("source", ""), "text": d}
            for d, m in zip(documents, metadatas)
        ],
    }
