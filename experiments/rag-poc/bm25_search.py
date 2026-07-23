"""BM25 關鍵字檢索，補 dense embedding 語意檢索漏接精確詞彙的洞。

Dense embedding 抓「意思像不像」，BM25 抓「用詞重不重疊」，兩者互補：
實測「戒菸專線幾號」這題，dense 候選池前 10 名完全沒撈到含
「0800-636363」那篇文章——因為那句話只是文章裡一小段，被其他段落的
語意稀釋掉了；BM25 靠「戒菸」「專線」關鍵字重疊分數就能直接命中。

用 jieba 斷詞（BM25 需要詞而不是整串中文字），全部 chunk 只在第一次
呼叫時建索引一次（語料量小，記憶體建索引比維護額外的持久化存檔簡單）。
"""

import jieba
from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    return [t for t in jieba.cut(text) if t.strip()]


class BM25Index:
    def __init__(self, ids: list[str], documents: list[str], metadatas: list[dict]):
        self.ids = ids
        self.documents = documents
        self.metadatas = metadatas
        self._bm25 = BM25Okapi([tokenize(doc) for doc in documents])

    def search(self, query: str, top_k: int) -> list[dict]:
        scores = self._bm25.get_scores(tokenize(query))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            {
                "id": self.ids[i],
                "document": self.documents[i],
                "metadata": self.metadatas[i],
            }
            for i in top_indices
            if scores[i] > 0  # 分數 0 代表完全沒有關鍵字重疊，不算候選
        ]
