"""SageMaker ASR adapter 的契約、錯誤分類與安全邊界。"""

from __future__ import annotations

import io
import json
import time

import pytest

from src.shared.asr.providers import RemoteEndpointSpec, SageMakerAsrProvider
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
)

AUDIO = CanonicalAudio(
    pcm_s16le=b"\x01\x02" * 1600,
    sample_rate_hz=16_000,
    channels=1,
    sample_width_bits=16,
    duration_ms=200,
    input_format=InputFormat.M4A,
)
CONTEXT = CorrelationContext("corr-remote")
SPEC = RemoteEndpointSpec("asr-ce", "model-id", "revision")


class FakeClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = {"text": "遠端辨識結果"} if payload is None else payload
        self.error = error
        self.calls: list[dict] = []

    def invoke_endpoint(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        body = self.payload if isinstance(self.payload, bytes) else json.dumps(
            self.payload
        ).encode()
        return {"Body": io.BytesIO(body)}


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("sensitive provider detail")
        self.response = {"Error": {"Code": code}}


class ReadTimeoutError(Exception):
    pass


def build(client: FakeClient, languages=None) -> SageMakerAsrProvider:
    return SageMakerAsrProvider(
        "ce_remote",
        SPEC,
        languages or frozenset({Language.ZH_TW, Language.HAK}),
        client,
    )


def run(provider: SageMakerAsrProvider, **overrides):
    values = {
        "audio": AUDIO,
        "language": Language.ZH_TW,
        "deadline": Deadline.after(5, time.monotonic),
        "cancellation": CancellationSignal(),
        "context": CONTEXT,
    }
    values.update(overrides)
    return provider.transcribe(**values)


def test_contract_sends_only_canonical_audio_and_allowed_metadata() -> None:
    client = FakeClient()
    result = run(build(client))

    assert isinstance(result, Transcript)
    assert result.text == "遠端辨識結果"
    assert client.calls == [
        {
            "EndpointName": "asr-ce",
            "ContentType": "application/octet-stream",
            "Accept": "application/json",
            "Body": AUDIO.pcm_s16le,
            "CustomAttributes": "language=zh-TW;sample_rate_hz=16000;channels=1",
        }
    ]


def test_unsupported_language_does_not_call_endpoint() -> None:
    client = FakeClient()
    result = run(build(client, frozenset({Language.HAK})))

    assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert client.calls == []


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
def test_transient_errors_are_available_for_fallback(code: str) -> None:
    result = run(build(FakeClient(error=ClientError(code))))
    assert result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    assert "sensitive" not in result.message


def test_timeout_is_terminal_deadline_error() -> None:
    result = run(build(FakeClient(error=ReadTimeoutError())))
    assert result.category is AsrErrorCategory.DEADLINE_EXCEEDED


def test_unknown_error_is_safe_provider_failure() -> None:
    result = run(build(FakeClient(error=RuntimeError("secret-endpoint"))))
    assert result.category is AsrErrorCategory.PROVIDER_FAILURE
    assert "secret-endpoint" not in result.message


@pytest.mark.parametrize(
    "payload", [b"not-json", [], {}, {"text": None}, {"text": "   "}]
)
def test_invalid_responses_are_rejected(payload) -> None:
    result = run(build(FakeClient(payload=payload)))
    assert result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_cancelled_or_expired_request_does_not_call_endpoint() -> None:
    client = FakeClient()
    cancelled = CancellationSignal()
    cancelled.trigger()

    assert run(build(client), cancellation=cancelled).category is AsrErrorCategory.CANCELLED
    assert run(
        build(client), deadline=Deadline.after(-1, time.monotonic)
    ).category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert client.calls == []


def test_postflight_cancellation_never_exposes_transcript() -> None:
    cancellation = CancellationSignal()

    class CancellingClient(FakeClient):
        def invoke_endpoint(self, **kwargs):
            cancellation.trigger()
            return super().invoke_endpoint(**kwargs)

    result = run(build(CancellingClient()), cancellation=cancellation)
    assert result.category is AsrErrorCategory.CANCELLED
    assert "遠端辨識結果" not in result.message


def test_spec_rejects_blank_values_and_invalid_timeouts() -> None:
    with pytest.raises(ValueError):
        RemoteEndpointSpec("", "model", "revision")
    with pytest.raises(ValueError):
        RemoteEndpointSpec("endpoint", "model", "revision", read_timeout_seconds=0)
