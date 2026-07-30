"""
遠端端點 provider 測試 — SageMaker 呼叫的錯誤分類與輸入邊界。

以假 client 取代 boto3，不建立任何網路連線（conftest 也會阻斷 socket）。
"""
from __future__ import annotations

import io
import json
import time

import pytest

from src.shared.asr.concurrency import ModelSlotPool
from src.shared.asr.remote_endpoints import (
    RemoteEndpointSpec,
    SageMakerAsrProvider,
)
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

CONTEXT = CorrelationContext(correlation_id="corr-remote-1")
AUDIO = CanonicalAudio(
    pcm_s16le=b"\x01\x02" * 1600,
    sample_rate_hz=16_000,
    channels=1,
    sample_width_bits=16,
    duration_ms=200,
    input_format=InputFormat.M4A,
)
SPEC = RemoteEndpointSpec(
    endpoint_name="ai-elder-care-asr-ce",
    model_id="adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
    revision="v2.0",
)


def deadline_in(seconds: float) -> Deadline:
    return Deadline.after(seconds, time.monotonic)


class FakeSageMakerClient:
    """記錄呼叫參數的假 client；可設定回應內容或拋出例外。"""

    def __init__(self, payload=None, raise_exc: Exception | None = None) -> None:
        self._payload = {"text": "遠端辨識結果"} if payload is None else payload
        self._raise = raise_exc
        self.calls: list[dict] = []

    def invoke_endpoint(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        if isinstance(self._payload, (bytes, str)):
            body = (
                self._payload
                if isinstance(self._payload, bytes)
                else self._payload.encode()
            )
        else:
            body = json.dumps(self._payload).encode()
        return {"Body": io.BytesIO(body)}


def build(client: FakeSageMakerClient, capacity: int = 1) -> SageMakerAsrProvider:
    provider = SageMakerAsrProvider(
        provider_id="ce_remote",
        spec=SPEC,
        slot_pool=ModelSlotPool("ce_remote", capacity),
        supported_languages=frozenset({Language.ZH_TW, Language.HAK}),
    )
    # 直接注入假 client，繞過 boto3：測試不得建立真實 SDK client。
    provider._handle._handle = client  # type: ignore[attr-defined]
    return provider


def run(provider: SageMakerAsrProvider, **overrides):
    kwargs = {
        "audio": AUDIO,
        "language": Language.ZH_TW,
        "deadline": deadline_in(5.0),
        "cancellation": CancellationSignal(),
        "context": CONTEXT,
        "max_queue_wait_seconds": 1.0,
    }
    kwargs.update(overrides)
    return provider.transcribe_with_admission(
        kwargs["audio"],
        kwargs["language"],
        kwargs["deadline"],
        kwargs["cancellation"],
        kwargs["context"],
        kwargs["max_queue_wait_seconds"],
    )


class ClientError(Exception):
    """模擬 botocore ClientError 的結構（帶 response.Error.Code）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "internal detail"}}


class ReadTimeoutError(Exception):
    """模擬 botocore 的讀取逾時（靠類別名稱辨識）。"""


# ─────────────────────────────────────────────────────────────────
# 規格驗證
# ─────────────────────────────────────────────────────────────────
def test_spec_rejects_blank_endpoint_name() -> None:
    with pytest.raises(ValueError):
        RemoteEndpointSpec(endpoint_name="  ", model_id="m", revision="r")


def test_spec_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        RemoteEndpointSpec(
            endpoint_name="e", model_id="m", revision="r", read_timeout_seconds=0
        )


def test_provider_requires_at_least_one_language() -> None:
    with pytest.raises(ValueError):
        SageMakerAsrProvider(
            provider_id="x",
            spec=SPEC,
            slot_pool=ModelSlotPool("x", 1),
            supported_languages=frozenset(),
        )


# ─────────────────────────────────────────────────────────────────
# 成功路徑與輸入邊界
# ─────────────────────────────────────────────────────────────────
def test_successful_invocation_returns_transcript() -> None:
    client = FakeSageMakerClient()
    record = run(build(client))

    assert isinstance(record.result, Transcript)
    assert record.result.text == "遠端辨識結果"
    assert record.admitted is True
    assert len(client.calls) == 1


def test_only_canonical_pcm_and_allowed_fields_are_sent() -> None:
    """送出的內容不得含原始音訊容器、token、prompt 或 endpoint 以外的識別資訊。"""
    client = FakeSageMakerClient()
    run(build(client))

    call = client.calls[0]
    assert call["EndpointName"] == SPEC.endpoint_name
    assert call["Body"] == AUDIO.pcm_s16le
    assert call["ContentType"] == "application/octet-stream"

    attributes = call["CustomAttributes"]
    assert "language=zh-TW" in attributes
    assert "sample_rate_hz=16000" in attributes
    # 不得夾帶敏感或無關資訊
    for forbidden in ("token", "prompt", "htia_", "correlation", "elder"):
        assert forbidden not in attributes.lower()

    # 送出的欄位集合是封閉的
    assert set(call) == {
        "EndpointName",
        "ContentType",
        "Accept",
        "Body",
        "CustomAttributes",
    }


def test_unsupported_language_is_route_not_approved_without_calling_endpoint() -> None:
    client = FakeSageMakerClient()
    provider = SageMakerAsrProvider(
        provider_id="formo_remote",
        spec=SPEC,
        slot_pool=ModelSlotPool("formo_remote", 1),
        supported_languages=frozenset({Language.HAK}),
    )
    provider._handle._handle = client  # type: ignore[attr-defined]

    record = run(provider, language=Language.ZH_TW)

    assert record.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert client.calls == []


# ─────────────────────────────────────────────────────────────────
# 錯誤分類
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "code",
    [
        "ThrottlingException",
        "ModelNotReadyException",
        "ServiceUnavailable",
        "InternalFailure",
        "ModelError",
    ],
)
def test_transient_endpoint_errors_become_provider_unavailable(code) -> None:
    """暫時性錯誤必須是可轉移的，備援鏈才會換下一個端點。"""
    record = run(build(FakeSageMakerClient(raise_exc=ClientError(code))))

    assert isinstance(record.result, TypedAsrError)
    assert record.result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    assert "internal detail" not in record.result.message


def test_validation_error_becomes_provider_failure() -> None:
    """我們自己送錯 payload 不是端點不可用，換端點也不會好。"""
    record = run(
        build(FakeSageMakerClient(raise_exc=ClientError("ValidationError")))
    )
    assert record.result.category is AsrErrorCategory.PROVIDER_FAILURE


def test_read_timeout_becomes_deadline_exceeded() -> None:
    record = run(build(FakeSageMakerClient(raise_exc=ReadTimeoutError())))
    assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert record.result.retryable is True


def test_unclassified_exception_does_not_leak_message() -> None:
    record = run(
        build(
            FakeSageMakerClient(
                raise_exc=RuntimeError("arn:aws:sagemaker:secret-endpoint")
            )
        )
    )
    assert record.result.category is AsrErrorCategory.PROVIDER_FAILURE
    assert "secret-endpoint" not in record.result.message


# ─────────────────────────────────────────────────────────────────
# 回應解析
# ─────────────────────────────────────────────────────────────────
def test_non_json_response_is_invalid_response() -> None:
    record = run(build(FakeSageMakerClient(payload=b"<html>oops</html>")))
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_json_array_response_is_invalid_response() -> None:
    record = run(build(FakeSageMakerClient(payload=["not", "an", "object"])))
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": 5}])
def test_missing_or_blank_text_is_invalid_response(payload) -> None:
    record = run(build(FakeSageMakerClient(payload=payload)))
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


# ─────────────────────────────────────────────────────────────────
# 終態優先權與併發
# ─────────────────────────────────────────────────────────────────
def test_cancelled_before_call_does_not_invoke_endpoint() -> None:
    client = FakeSageMakerClient()
    cancelled = CancellationSignal()
    cancelled.trigger()

    record = run(build(client), cancellation=cancelled)

    assert record.result.category is AsrErrorCategory.CANCELLED
    assert client.calls == []


def test_expired_deadline_does_not_invoke_endpoint() -> None:
    client = FakeSageMakerClient()
    record = run(build(client), deadline=deadline_in(-1.0))

    assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert client.calls == []


def test_saturated_provider_does_not_invoke_endpoint() -> None:
    """本地 slot pool 限制單一端點的同時外呼數，滿了就回報容量問題。"""
    import threading

    class SlowClient(FakeSageMakerClient):
        def invoke_endpoint(self, **kwargs):
            time.sleep(0.3)
            return super().invoke_endpoint(**kwargs)

    provider = build(SlowClient(), capacity=1)
    holder = threading.Thread(target=lambda: run(provider))
    holder.start()
    time.sleep(0.08)

    denied = run(provider, max_queue_wait_seconds=0.0)
    holder.join()

    assert denied.admitted is False
    assert denied.result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
