"""Bedrock 呼叫層測試。

重點在錯誤分類（決定 batch 是 throw 還是標 failed）、structured outputs 降級路徑，
以及 JSON 解析容錯。
"""

import json

import pytest
from botocore.exceptions import ParamValidationError

from src.shared import bedrock
from tests.conftest import FakeConverseClient, client_error

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def test_converse_uses_structured_output_when_schema_given():
    client = FakeConverseClient('{"answer": "好"}')
    data, metadata = bedrock.converse_json("問題", SCHEMA, client=client, schema_name="Demo")

    assert data == {"answer": "好"}
    assert metadata["structured_output"] is True
    request = client.requests[0]
    assert request["outputConfig"]["textFormat"]["type"] == "json_schema"
    assert request["outputConfig"]["textFormat"]["jsonSchema"]["name"] == "Demo"
    assert request["inferenceConfig"]["temperature"] == 0.0


def test_system_prompt_is_passed():
    client = FakeConverseClient('{"answer": "好"}')
    bedrock.converse("問題", system="你是助手", client=client)
    assert client.requests[0]["system"] == [{"text": "你是助手"}]


def test_structured_output_degrades_when_unsupported():
    """botocore 或模型不支援時要能完成工作，並在 metadata 留下訊號。"""
    client = FakeConverseClient(
        '{"answer": "好"}',
        errors=[client_error("ValidationException", "Unknown parameter outputConfig")],
    )
    data, metadata = bedrock.converse_json("問題", SCHEMA, client=client)

    assert data == {"answer": "好"}
    assert metadata["structured_output"] is False
    assert "outputConfig" not in client.requests[1]
    # 降級後 prompt 要自己承載「只輸出 JSON」的要求
    assert "只輸出符合上述 JSON Schema" in client.requests[1]["messages"][0]["content"][0]["text"]


def test_param_validation_error_also_degrades():
    error = ParamValidationError(report="Unknown parameter in input: \"outputConfig\"")
    client = FakeConverseClient('{"answer": "好"}', errors=[error])
    data, metadata = bedrock.converse_json("問題", SCHEMA, client=client)
    assert data == {"answer": "好"}
    assert metadata["structured_output"] is False


def test_permanent_error_is_not_retried():
    client = FakeConverseClient(errors=[client_error("AccessDeniedException")])
    with pytest.raises(bedrock.PermanentBedrockError):
        bedrock.converse("問題", client=client)
    assert len(client.requests) == 1


def test_throttling_is_retried_then_raises_retryable(monkeypatch):
    monkeypatch.setattr(bedrock, "MAX_ATTEMPTS", 3)
    client = FakeConverseClient(errors=[client_error("ThrottlingException")] * 3)
    with pytest.raises(bedrock.RetryableBedrockError):
        bedrock.converse("問題", client=client)
    assert len(client.requests) == 3


def test_throttling_recovers_on_retry(monkeypatch):
    monkeypatch.setattr(bedrock, "MAX_ATTEMPTS", 3)
    client = FakeConverseClient('{"answer": "好"}', errors=[client_error("ThrottlingException")])
    data, _ = bedrock.converse_json("問題", SCHEMA, client=client)
    assert data == {"answer": "好"}
    assert len(client.requests) == 2


def test_unknown_error_code_is_treated_as_retryable(caplog):
    client = FakeConverseClient(errors=[client_error("SomeBrandNewException")] * bedrock.MAX_ATTEMPTS)
    with caplog.at_level("WARNING"):
        with pytest.raises(bedrock.RetryableBedrockError):
            bedrock.converse("問題", client=client)
    assert "未分類的 Bedrock 錯誤碼" in caplog.text


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('說明文字 {"a": 1} 後綴', {"a": 1}),
        ('[{"a": 1}]', {"a": 1}),
        ("", {}),
        ("完全不是 JSON", {}),
    ],
)
def test_extract_json(text, expected):
    assert bedrock.extract_json(text) == expected


def test_unparseable_json_is_retryable():
    client = FakeConverseClient("我沒有回 JSON")
    with pytest.raises(bedrock.RetryableBedrockError, match="無法|可解析"):
        bedrock.converse_json("問題", SCHEMA, client=client)


# -- embedding ----------------------------------------------------------------


def test_titan_embedding_sends_one_text_per_call():
    client = FakeConverseClient()
    client.embeddings = [[0.1, 0.2], [0.3, 0.4]]
    provider = bedrock.BedrockEmbeddingProvider("amazon.titan-embed-text-v2:0", 2, client=client)

    vectors = provider.embed_documents(["甲", "乙"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert len(client.invoke_payloads) == 2
    assert client.invoke_payloads[0] == {"inputText": "甲", "dimensions": 2, "normalize": True}


def test_cohere_embedding_batches_and_marks_input_type():
    client = FakeConverseClient()
    client.embeddings = [[0.1], [0.2]]
    provider = bedrock.BedrockEmbeddingProvider("cohere.embed-multilingual-v3", 1, client=client)

    vectors = provider.embed_documents(["甲", "乙"])
    assert vectors == [[0.1], [0.2]]
    assert len(client.invoke_payloads) == 1
    assert client.invoke_payloads[0]["input_type"] == "search_document"

    client.embeddings = [[0.5]]
    assert provider.embed_query("問") == [0.5]
    assert client.invoke_payloads[1]["input_type"] == "search_query"


def test_embedding_of_empty_input_skips_call():
    client = FakeConverseClient()
    provider = bedrock.BedrockEmbeddingProvider("amazon.titan-embed-text-v2:0", 2, client=client)
    assert provider.embed_documents([]) == []
    assert client.invoke_payloads == []
