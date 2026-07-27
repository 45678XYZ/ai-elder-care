"""概念檢索：把對話塊對應到 Top-K 候選細分類節點。

改寫自 aws-hackathon 的 `dense_retriever`。原版在本機載 `sentence-transformers`+`torch`
把 147 個 sub-chunk 算成矩陣後做內積；那套進不了 Lambda，也和「embedding 模型要能抽換」
的決策衝突。這裡的作法：

- 向量索引放 **S3 Vectors**（決策 B），查詢向量由 Bedrock embedding 產生。
- 索引維度在建立時固定，所以 index 名稱帶模型與維度，換模型是新建索引並存、切 env 生效。
- **保留「每個 concept 取其 sub-chunk 最高相似度」的聚合**。一個概念有定義／範例／同義詞
  三個 sub-chunk，任一命中就代表該概念相關；用最大值而非平均，否則描述較長的概念會被稀釋。
- 索引不可用時**降級為離線內積**（用同一個 embedder 現算 sub-chunk 向量）。降級路徑同時是
  單元測試路徑：注入 stub embedder 就能離線驗證聚合與 Top-K 排序。
- 只檢索 level ≥ 2 的節點。根節點與領域節點粗到無法對應高階類別，剪枝階段本來就會丟掉，
  讓它們占用 Top-K 名額只是浪費候選。
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

CONCEPT_CHUNKS_FILE = "concept_chunks.jsonl"

# 每個概念的 sub-chunk 數（定義／範例／同義詞）；線上查詢要多取幾筆才夠聚合出 Top-K 概念
SUBCHUNKS_PER_CONCEPT = 3

# S3 Vectors 單次 put 的上限
PUT_VECTORS_BATCH_SIZE = 500


class RetrievalError(RuntimeError):
    """檢索資產缺失或設定不完整。"""


@dataclass(frozen=True)
class ConceptChunk:
    """概念的單一 sub-chunk；embedding 的最小單位。"""

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
    """載入概念 sub-chunk 資產。"""
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
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return list(vector)
    return [value / norm for value in vector]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class ConceptRetriever:
    """概念檢索器；線上走 S3 Vectors，不可用時降級為離線內積。"""

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

    # -- 線上／離線兩條路徑 -------------------------------------------------

    @property
    def online_enabled(self) -> bool:
        return bool(self.vector_bucket and self.index_name and self._s3vectors_client is not None)

    def _query_online(self, query_vector: Sequence[float], limit: int) -> dict[str, float]:
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
                # 沒有 metadata 時退回由 key 推導（key 格式為 `<concept_id>#<aspect>`）
                concept_id = str(vector.get("key", "")).split("#")[0]
            if not concept_id:
                continue
            # 索引距離度量為 cosine，相似度取 1 - distance
            similarity = 1.0 - float(vector.get("distance", 1.0))
            scores[concept_id] = max(scores.get(concept_id, -1.0), similarity)
        return scores

    def _offline_vectors(self) -> tuple[list[float], ...]:
        if self._offline_matrix is None:
            logger.info("計算離線概念向量：chunks=%s", len(self.chunks))
            vectors = self.embedder.embed_documents([chunk.embedding_text for chunk in self.chunks])
            self._offline_matrix = tuple(_normalize(vector) for vector in vectors)
        return self._offline_matrix

    def _query_offline(self, query_vector: Sequence[float]) -> dict[str, float]:
        matrix = self._offline_vectors()
        scores: dict[str, float] = {}
        for chunk, vector in zip(self.chunks, matrix):
            similarity = _dot(query_vector, vector)
            scores[chunk.concept_id] = max(scores.get(chunk.concept_id, -1.0), similarity)
        return scores

    # -- 對外 ---------------------------------------------------------------

    def retrieve(self, query_text: str, top_k: int | None = None) -> tuple[CandidateConcept, ...]:
        """回傳依相似度遞減排序的 Top-K 候選概念。"""
        # 明確傳 0 代表「不要候選」，不能被 or 運算吃掉退回預設值
        limit = self.top_k if top_k is None else top_k
        if limit <= 0 or not query_text.strip():
            return ()

        query_vector = _normalize(self.embedder.embed_query(query_text))

        scores: dict[str, float] = {}
        if self.online_enabled:
            try:
                scores = self._query_online(query_vector, limit * SUBCHUNKS_PER_CONCEPT)
            except (ClientError, BotoCoreError, KeyError) as exc:
                # 索引不存在或權限不足時不讓整個 batch 掛掉，改用離線內積
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
    """產生 S3 Vectors 的 put_vectors payload。

    metadata 帶 `concept_id` 供查詢端聚合；key 用 `chunk_id`，重跑索引時是覆寫而非新增。
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
    """分批寫入向量；回傳寫入筆數。"""
    written = 0
    for start in range(0, len(payload), PUT_VECTORS_BATCH_SIZE):
        batch = list(payload[start : start + PUT_VECTORS_BATCH_SIZE])
        client.put_vectors(vectorBucketName=vector_bucket, indexName=index_name, vectors=batch)
        written += len(batch)
    return written
