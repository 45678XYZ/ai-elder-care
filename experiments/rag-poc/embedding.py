"""共用的多語言 embedding function。

Chroma 內建預設 embedding（all-MiniLM-L6-v2）幾乎是英文語料訓練，
對繁體中文語意檢索效果很差（實測「高血壓」「喘息服務」等問題撈不到對應文件）。
改用同樣可本地免費跑、不需要額外 API key 的多語言 sentence-transformers 模型。

ingest.py 與 rag.py 都必須透過這裡取得同一個 embedding function，
否則建索引與查詢用的向量空間不一致，檢索會整個失準。
"""

from chromadb.utils import embedding_functions

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_embedding_function = None


def get_embedding_function():
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME
        )
    return _embedding_function
