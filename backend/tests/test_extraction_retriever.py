"""概念檢索測試。

以 stub embedder 驗證聚合與排序（離線路徑），以假 s3vectors client 驗證線上路徑與降級。
"""

import pytest
from botocore.exceptions import ClientError

from src.extraction.models import CandidateConcept
from src.extraction.retriever import (
    ConceptChunk,
    ConceptRetriever,
    RetrievalError,
    build_index_payload,
    load_concept_chunks,
    put_index_payload,
)
from src.extraction.taxonomy import load_taxonomy
from tests.conftest import StubEmbeddingProvider

SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"
FALL = "UCO.StatusOutcome.SafetyIncident.PhysicalFall"


class FakeVectorClient:
    def __init__(self, vectors=None, error=None):
        self.vectors = vectors or []
        self.error = error
        self.queries = []
        self.puts = []

    def query_vectors(self, **kwargs):
        self.queries.append(kwargs)
        if self.error:
            raise self.error
        return {"vectors": self.vectors, "distanceMetric": "cosine"}

    def put_vectors(self, **kwargs):
        self.puts.append(kwargs)
        return {}


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def chunks():
    return (
        ConceptChunk(f"{SCHEDULED}#def", SCHEDULED, "按時服藥", "definition", "按時服藥定義", 3),
        ConceptChunk(f"{SCHEDULED}#syn", SCHEDULED, "按時服藥", "synonyms", "吃藥 服藥", 3),
        ConceptChunk(f"{VITAL}#def", VITAL, "生理數據量測紀錄", "definition", "量血壓血糖", 3),
        ConceptChunk(f"{FALL}#def", FALL, "軀體跌倒與近跌事故", "definition", "跌倒滑倒", 3),
    )


# -- 資產 ---------------------------------------------------------------------


def test_real_asset_loads_and_skips_shallow_nodes():
    chunks = load_concept_chunks()
    concepts = {chunk.concept_id for chunk in chunks}
    # 根節點與領域節點不進候選：剪枝階段本來就會丟，占 Top-K 名額只是浪費
    assert "UCO" not in concepts
    assert "UCO.BehavioralRecord" not in concepts
    assert len(concepts) == 45
    assert all(chunk.level >= 2 for chunk in chunks)
    assert all(chunk.embedding_text for chunk in chunks)


def test_missing_asset_raises(tmp_path):
    with pytest.raises(RetrievalError, match="資產缺失"):
        load_concept_chunks(tmp_path)


# -- 離線聚合 -----------------------------------------------------------------


def test_offline_retrieval_aggregates_max_per_concept(taxonomy, chunks):
    # 查詢向量與「吃藥 服藥」完全相同，該 sub-chunk 相似度為 1
    embedder = StubEmbeddingProvider()
    retriever = ConceptRetriever(taxonomy, embedder, chunks=chunks, top_k=3)

    candidates = retriever.retrieve("吃藥 服藥")
    assert candidates[0].concept_id == SCHEDULED
    assert candidates[0].similarity == pytest.approx(1.0, abs=1e-6)
    # 同一概念的兩個 sub-chunk 只回一筆，取較高分者
    assert [candidate.concept_id for candidate in candidates].count(SCHEDULED) == 1
    assert len(candidates) == 3


def test_retrieval_is_deterministic_and_sorted(taxonomy, chunks):
    embedder = StubEmbeddingProvider()
    retriever = ConceptRetriever(taxonomy, embedder, chunks=chunks, top_k=4)
    first = retriever.retrieve("量血壓血糖")
    second = ConceptRetriever(taxonomy, StubEmbeddingProvider(), chunks=chunks, top_k=4).retrieve(
        "量血壓血糖"
    )
    assert [c.concept_id for c in first] == [c.concept_id for c in second]
    scores = [c.similarity for c in first]
    assert scores == sorted(scores, reverse=True)
    assert first[0].concept_id == VITAL


def test_candidates_carry_taxonomy_metadata(taxonomy, chunks):
    retriever = ConceptRetriever(taxonomy, StubEmbeddingProvider(), chunks=chunks, top_k=1)
    candidate = retriever.retrieve("跌倒滑倒")[0]
    assert isinstance(candidate, CandidateConcept)
    assert candidate.concept_id == FALL
    # 定義與同義詞取自分類體系，不是檢索資產，確保 prompt 與分類體系同源
    assert candidate.definition == taxonomy.get(FALL).definition
    assert candidate.synonyms == taxonomy.get(FALL).synonyms


def test_top_k_limits_results(taxonomy, chunks):
    retriever = ConceptRetriever(taxonomy, StubEmbeddingProvider(), chunks=chunks, top_k=14)
    assert len(retriever.retrieve("吃藥", top_k=2)) == 2
    assert retriever.retrieve("吃藥", top_k=0) == ()
    assert retriever.retrieve("   ") == ()


def test_offline_vectors_are_computed_once(taxonomy, chunks):
    embedder = StubEmbeddingProvider()
    retriever = ConceptRetriever(taxonomy, embedder, chunks=chunks, top_k=2)
    retriever.retrieve("吃藥")
    retriever.retrieve("跌倒")
    assert embedder.document_calls == 1
    assert embedder.query_calls == 2


# -- 線上路徑 -----------------------------------------------------------------


def online_retriever(taxonomy, chunks, client):
    return ConceptRetriever(
        taxonomy,
        StubEmbeddingProvider(),
        chunks=chunks,
        top_k=2,
        vector_bucket="bucket",
        index_name="uco-concepts-stub-8",
        s3vectors_client=client,
    )


def test_online_query_uses_index_and_converts_distance(taxonomy, chunks):
    client = FakeVectorClient(
        vectors=[
            {"key": f"{FALL}#def", "distance": 0.1, "metadata": {"concept_id": FALL}},
            {"key": f"{VITAL}#def", "distance": 0.4, "metadata": {"concept_id": VITAL}},
            {"key": f"{FALL}#syn", "distance": 0.6, "metadata": {"concept_id": FALL}},
        ]
    )
    retriever = online_retriever(taxonomy, chunks, client)
    candidates = retriever.retrieve("跌倒了")

    assert [c.concept_id for c in candidates] == [FALL, VITAL]
    # cosine：相似度 = 1 - distance；同概念取最大值（0.9 而非 0.4）
    assert candidates[0].similarity == pytest.approx(0.9)
    assert candidates[1].similarity == pytest.approx(0.6)

    query = client.queries[0]
    assert query["vectorBucketName"] == "bucket"
    assert query["indexName"] == "uco-concepts-stub-8"
    assert query["returnMetadata"] is True
    # 每概念有多個 sub-chunk，線上要多取幾筆才夠聚合出 Top-K 概念
    assert query["topK"] == 2 * 3
    assert len(query["queryVector"]["float32"]) == 8


def test_online_falls_back_to_key_when_metadata_missing(taxonomy, chunks):
    client = FakeVectorClient(vectors=[{"key": f"{VITAL}#def", "distance": 0.2}])
    candidates = online_retriever(taxonomy, chunks, client).retrieve("量血壓")
    assert [c.concept_id for c in candidates] == [VITAL]


def test_online_failure_degrades_to_offline(taxonomy, chunks, caplog):
    """索引不存在或權限不足時不能讓整個 batch 掛掉。"""
    error = ClientError({"Error": {"Code": "NotFoundException", "Message": "no index"}}, "QueryVectors")
    client = FakeVectorClient(error=error)
    retriever = online_retriever(taxonomy, chunks, client)

    with caplog.at_level("WARNING"):
        candidates = retriever.retrieve("吃藥 服藥")
    assert candidates[0].concept_id == SCHEDULED
    assert "降級為離線內積" in caplog.text


def test_empty_online_result_degrades_to_offline(taxonomy, chunks):
    client = FakeVectorClient(vectors=[])
    candidates = online_retriever(taxonomy, chunks, client).retrieve("吃藥 服藥")
    assert candidates and candidates[0].concept_id == SCHEDULED


def test_unknown_concept_from_index_is_skipped(taxonomy, chunks, caplog):
    client = FakeVectorClient(
        vectors=[
            {"key": "UCO.Removed.Node#def", "distance": 0.0, "metadata": {"concept_id": "UCO.Removed.Node"}},
            {"key": f"{FALL}#def", "distance": 0.3, "metadata": {"concept_id": FALL}},
        ]
    )
    with caplog.at_level("WARNING"):
        candidates = online_retriever(taxonomy, chunks, client).retrieve("跌倒")
    assert [c.concept_id for c in candidates] == [FALL]
    assert "不在分類體系內" in caplog.text


# -- 索引建置 -----------------------------------------------------------------


def test_build_index_payload_carries_concept_metadata(chunks):
    payload = build_index_payload(chunks, StubEmbeddingProvider())
    assert len(payload) == len(chunks)
    first = payload[0]
    assert first["key"] == chunks[0].chunk_id
    assert first["metadata"]["concept_id"] == chunks[0].concept_id
    assert len(first["data"]["float32"]) == 8
    # 向量已正規化，查詢端才能直接用 1 - distance 換算
    norm = sum(value**2 for value in first["data"]["float32"]) ** 0.5
    assert norm == pytest.approx(1.0)


def test_put_index_payload_batches(chunks):
    client = FakeVectorClient()
    payload = build_index_payload(chunks, StubEmbeddingProvider())
    written = put_index_payload(client, "bucket", "index", payload)
    assert written == len(payload)
    assert len(client.puts) == 1
    assert client.puts[0]["indexName"] == "index"


def test_put_index_payload_splits_large_payload():
    client = FakeVectorClient()
    payload = [{"key": str(index)} for index in range(1200)]
    written = put_index_payload(client, "bucket", "index", payload)
    assert written == 1200
    assert [len(call["vectors"]) for call in client.puts] == [500, 500, 200]
