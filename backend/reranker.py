"""Cross-encoder reranker：對「先撈寬、再篩準」的檢索流程做第二輪排序。

向量檢索（dense embedding）是 bi-encoder，查詢和文件分開編碼，
只能算「大概像不像」，容易讓語意相近但答非所問的段落排到真正對的
段落前面（例如「代謝症候群」的預防技巧排到「糖尿病」的診斷標準前面）。
Cross-encoder 把查詢和文件一起餵進去比對，精準度高很多，但每筆都要
重新算一次，較慢——所以只用來對 rag.py 已經撈回來的候選集重排，
不是拿來取代向量檢索本身。
"""

from sentence_transformers import CrossEncoder

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

_reranker = None


def get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(MODEL_NAME)
    return _reranker


def rerank(question: str, documents: list[str]) -> list[int]:
    """回傳 documents 依相關性由高到低排序後的原始 index 順序。"""
    if not documents:
        return []
    scores = get_reranker().predict([[question, doc] for doc in documents])
    return sorted(range(len(documents)), key=lambda i: scores[i], reverse=True)
