"""共用測試工具。"""

import io
import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from src.shared import bedrock


class FakeConverseClient:
    """假的 bedrock-runtime client。

    以「回傳文字」與「要拋的例外序列」驅動，讓 prompt 組裝、structured outputs 降級、
    錯誤分類與重試都能離線驗證，不需要網路或憑證。
    """

    def __init__(self, texts: list[str] | str | None = None, errors: list[Exception] | None = None):
        if isinstance(texts, str):
            texts = [texts]
        self.texts = list(texts or ["{}"])
        self.errors = list(errors or [])
        self.requests: list[dict[str, Any]] = []
        self.invoke_payloads: list[dict[str, Any]] = []
        self.embeddings: list[list[float]] = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
            "stopReason": "end_turn",
        }

    def invoke_model(self, **kwargs):
        payload = json.loads(kwargs["body"])
        self.invoke_payloads.append(payload)
        if self.errors:
            raise self.errors.pop(0)
        if "texts" in payload:
            body = {"embeddings": [self.embeddings.pop(0) for _ in payload["texts"]]}
        else:
            body = {"embedding": self.embeddings.pop(0)}
        return {"body": io.BytesIO(json.dumps(body).encode("utf-8"))}


def client_error(code: str, message: str = "boom", operation: str = "Converse") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


@pytest.fixture(autouse=True)
def no_bedrock_backoff(monkeypatch):
    """測試不真的睡；重試次數仍照設定跑。"""
    monkeypatch.setattr(bedrock, "_sleep_backoff", lambda attempt: None)


@pytest.fixture(autouse=True)
def quiet_metrics(monkeypatch):
    """預設關掉 EMF 輸出，避免測試輸出被指標 JSON 淹掉；test_metrics 自行開啟。"""
    monkeypatch.setenv("METRICS_ENABLED", "false")


@pytest.fixture(autouse=True)
def reset_bedrock_client():
    bedrock.reset_runtime_client()
    yield
    bedrock.reset_runtime_client()


class StubEmbeddingProvider:
    """確定性的假 embedder。

    以字元雜湊產生固定向量：同輸入必得同向量，可離線驗證 depth score 與檢索聚合，
    也讓 golden test 不依賴模型行為。
    """

    def __init__(self, dimension: int = 8, vectors: dict[str, list[float]] | None = None):
        self.model_id = "stub-embedding"
        self.dimension = dimension
        self.vectors = vectors or {}
        self.document_calls = 0
        self.query_calls = 0

    def _vector(self, text: str) -> list[float]:
        if text in self.vectors:
            return self.vectors[text]
        buckets = [0.0] * self.dimension
        for index, char in enumerate(text):
            buckets[(ord(char) + index) % self.dimension] += 1.0
        norm = sum(value * value for value in buckets) ** 0.5 or 1.0
        return [value / norm for value in buckets]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return self._vector(text)
