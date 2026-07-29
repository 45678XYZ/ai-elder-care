"""概念檢索模組：將對話片段檢索出 Top-K 候選細分類節點。

架構與設計決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

本模組設計目的：
- 為 Batch Extractor 提供二階段分類的第一階段（候選概念限縮），減少傳給 LLM 的分類空間。
- 採用 **S3 Vectors + Bedrock Embedding** 線上向量檢索，徹底移除對本機大模型套件 (PyTorch/sentence-transformers) 的依賴，符合 Lambda 部署條件。
- 支援 **「線上 S3 Vectors 檢索」與「離線內積計算」雙軌機制**，當向量索引未建立或服務異常時自動優雅降級，同時作為單元測試離線驗證路徑。
- 採用 **Max-Pooling（最大相似度聚合）** 策略，每個照護概念包含多個 sub-chunk（定義、同義詞、範例），取最高分者代表該概念分級，避免描述較長的概念被平均值稀釋。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import math

from botocore.exceptions import BotoCoreError, ClientError

from src.shared.bedrock import EmbeddingProvider

from .config import RETRIEVAL_ASSETS_DIR
from .models import CandidateConcept
from .pruner import MIN_CLASSIFIABLE_LEVEL
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 概念資產檔檔名
CONCEPT_CHUNKS_FILE = "concept_chunks.jsonl"

# 每個概念內建包含的 sub-chunk 數量上限（定義／同義詞／範例三類）；
# 線上 S3 Vectors 檢索時需向向量資料庫請求 (limit * 3) 筆向量，
# 以確保聚合去重後仍能提供足額的 Top-K 候選概念。
SUBCHUNKS_PER_CONCEPT = 3

# S3 Vectors 單次 put_vectors 寫入批次上限，避免請求 Payload 過大引發 API 失敗
PUT_VECTORS_BATCH_SIZE = 500


class RetrievalError(RuntimeError):
    """概念檢索資產缺失或設定無效時拋出的運行時錯誤。

    此錯誤代表檢索基礎設定有誤，需中止檢索初始化。
    """


@dataclass(frozen=True)
class ConceptChunk:
    """概念 sub-chunk 定義，為 Embedding 計算與檢索的最小物理單元。

    採用 frozen=True 確保切分塊在記憶體中具不可變性（Immutable），避免被誤修改。
    """

    chunk_id: str
    concept_id: str
    display_name: str
    aspect_type: str
    embedding_text: str
    level: int


def load_concept_chunks(
    assets_dir: Path | str | None = None,
    *,
    min_level: int = MIN_CLASSIFIABLE_LEVEL,
) -> tuple[ConceptChunk, ...]:
    """載入概念 sub-chunk 靜態資產檔。

    於載入時即剔除根節點與一級領域節點 (level < min_level)，因為過於粗粒度的節點
    無法對應至具體的 7 大高階事件類別，提前過濾可避免無效節點佔用 Top-K 候選名額。
    """
    base = Path(assets_dir) if assets_dir is not None else RETRIEVAL_ASSETS_DIR
    path = base / CONCEPT_CHUNKS_FILE
    if not path.is_file():
        raise RetrievalError(f"概念檢索資產缺失：{path}")

    chunks: list[ConceptChunk] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            level = int(item.get("level", str(item.get("concept_id", "")).count(".")))
            if level < min_level:
                continue
            text = item.get("embedding_text") or item.get("label_description_for_retrieval") or ""
            if not text:
                continue
            chunks.append(
                ConceptChunk(
                    chunk_id=item["chunk_id"],
                    concept_id=item["concept_id"],
                    display_name=item.get("display_name", ""),
                    aspect_type=item.get("aspect_type", ""),
                    embedding_text=text,
                    level=level,
                )
            )
    if not chunks:
        raise RetrievalError(f"概念檢索資產沒有可用的 sub-chunk：{path}")
    return tuple(chunks)


def _normalize(vector: Sequence[float]) -> list[float]:
    """進行 L2 向量正規化。

    單位化後的向量進行內積運算即可直接等於 Cosine 相似度，簡化後續的幾何相似度計算。
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return list(vector)
    return [value / norm for value in vector]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    """計算兩向量之內積。"""
    return sum(a * b for a, b in zip(left, right))


class ConceptRetriever:
    """候選概念檢索器。

    支援線上 S3 Vectors 檢索與離線內積計算雙軌機制。
    """

    def __init__(
        self,
        taxonomy: Taxonomy,
        embedder: EmbeddingProvider,
        *,
        chunks: Sequence[ConceptChunk] | None = None,
        top_k: int = 14,
        vector_bucket: str = "",
        index_name: str = "",
        s3vectors_client=None,
    ):
        self.taxonomy = taxonomy
        self.embedder = embedder
        self.chunks = tuple(chunks) if chunks is not None else load_concept_chunks()
        self.top_k = top_k
        self.vector_bucket = vector_bucket
        self.index_name = index_name
        self._s3vectors_client = s3vectors_client
        self._offline_matrix: tuple[list[float], ...] | None = None

    # -- 線上與離線雙軌檢索路徑 ----------------------------------------------

    @property
    def online_enabled(self) -> bool:
        """判定 S3 Vectors 線上檢索是否具備足夠的配置與 Client。"""
        return bool(self.vector_bucket and self.index_name and self._s3vectors_client is not None)

    def _query_online(self, query_vector: Sequence[float], limit: int) -> dict[str, float]:
        """呼叫 S3 Vectors API 執行線上近鄰搜尋。

        S3 Vectors 傳回之距離度量為 Cosine Distance，轉換為 Similarity 時使用 `1.0 - distance`。
        取同概念多個 sub-chunk 的最高相似度（Max-Pooling），確保命中的概念獲得最顯著得分。
        """
        response = self._s3vectors_client.query_vectors(
            vectorBucketName=self.vector_bucket,
            indexName=self.index_name,
            queryVector={"float32": list(query_vector)},
            topK=limit,
            returnDistance=True,
            returnMetadata=True,
        )
        scores: dict[str, float] = {}
        for vector in response.get("vectors") or []:
            metadata = vector.get("metadata") or {}
            concept_id = metadata.get("concept_id")
            if not concept_id:
                # 無 metadata 時容錯：由 Key 格式 `<concept_id>#<aspect>` 推導 concept_id
                concept_id = str(vector.get("key", "")).split("#")[0]
            if not concept_id:
                continue
            similarity = 1.0 - float(vector.get("distance", 1.0))
            scores[concept_id] = max(scores.get(concept_id, -1.0), similarity)
        return scores

    def _offline_vectors(self) -> tuple[list[float], ...]:
        """快取離線檢索所需的子區塊向量矩陣。

        將概念文字現算為 Embedding 矩陣並快取於記憶體，防止測試或降級模式下重複呼叫模型 API。
        """
        if self._offline_matrix is None:
            logger.info("計算離線概念向量：chunks=%s", len(self.chunks))
            vectors = self.embedder.embed_documents([chunk.embedding_text for chunk in self.chunks])
            self._offline_matrix = tuple(_normalize(vector) for vector in vectors)
        return self._offline_matrix

    def _query_offline(self, query_vector: Sequence[float]) -> dict[str, float]:
        """採用純記憶體內積計算相似度（降級 / 測試路徑）。"""
        matrix = self._offline_vectors()
        scores: dict[str, float] = {}
        for chunk, vector in zip(self.chunks, matrix):
            similarity = _dot(query_vector, vector)
            scores[chunk.concept_id] = max(scores.get(chunk.concept_id, -1.0), similarity)
        return scores

    # -- 對外檢索介面 --------------------------------------------------------

    def retrieve(self, query_text: str, top_k: int | None = None) -> tuple[CandidateConcept, ...]:
        """檢索與查詢文本最相關的 Top-K 候選概念，按相似度由高至低排序。

        若線上 S3 Vectors 發生權限不足或索引不存在時，自動降級為離線內積計算，
        確保主 Batch Extractor Pipeline 不因向量庫服務異常而整批中斷。
        """
        # 明確傳入 top_k=0 表示不請求候選，需避免被 Python bool(0)==False 退回預設值
        limit = self.top_k if top_k is None else top_k
        if limit <= 0 or not query_text.strip():
            return ()

        query_vector = _normalize(self.embedder.embed_query(query_text))

        scores: dict[str, float] = {}
        if self.online_enabled:
            try:
                scores = self._query_online(query_vector, limit * SUBCHUNKS_PER_CONCEPT)
            except (ClientError, BotoCoreError, KeyError) as exc:
                # 服務異常時降級為離線計算，防止整個 Batch 批次任務吞例外掛掉
                logger.warning(
                    "概念向量索引不可用，降級為離線內積：index=%s reason=%s", self.index_name, exc
                )
                scores = {}
        if not scores:
            scores = self._query_offline(query_vector)

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        candidates: list[CandidateConcept] = []
        for concept_id, similarity in ordered:
            node = self.taxonomy.get(concept_id)
            if node is None:
                logger.warning("檢索到不在分類體系內的節點，已略過：concept_id=%s", concept_id)
                continue
            candidates.append(
                CandidateConcept(
                    concept_id=node.concept_id,
                    display_name=node.display_name,
                    definition=node.definition,
                    retrieval_description=node.retrieval_description,
                    synonyms=node.synonyms,
                    similarity=round(similarity, 6),
                )
            )
        return tuple(candidates)


def build_index_payload(
    chunks: Sequence[ConceptChunk],
    embedder: EmbeddingProvider,
) -> list[dict]:
    """生成寫入 S3 Vectors 的向量 Payload 清單。

    使用 `chunk_id` 作為向量 Key，確保重複執行建置腳本時具覆寫冪等性（Idempotent），
    並於 metadata 攜帶 `concept_id` 供線上查詢端做 Max-Pooling 聚合。
    """
    vectors = embedder.embed_documents([chunk.embedding_text for chunk in chunks])
    payload = []
    for chunk, vector in zip(chunks, vectors):
        payload.append(
            {
                "key": chunk.chunk_id,
                "data": {"float32": _normalize(vector)},
                "metadata": {
                    "concept_id": chunk.concept_id,
                    "aspect_type": chunk.aspect_type,
                    "display_name": chunk.display_name,
                },
            }
        )
    return payload


def put_index_payload(
    client,
    vector_bucket: str,
    index_name: str,
    payload: Sequence[dict],
) -> int:
    """將向量 Payload 分批寫入 S3 Vectors 索引，回傳成功寫入筆數。

    自動依 `PUT_VECTORS_BATCH_SIZE` (500 筆) 切分 Payload 批次發送。
    """
    written = 0
    for start in range(0, len(payload), PUT_VECTORS_BATCH_SIZE):
        batch = list(payload[start : start + PUT_VECTORS_BATCH_SIZE])
        client.put_vectors(vectorBucketName=vector_bucket, indexName=index_name, vectors=batch)
        written += len(batch)
    return written

