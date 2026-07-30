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


# ─────────────────────────────────────────────────────────────────
# SageMaker 傳輸契約驗證（Task 3 強化）
# ─────────────────────────────────────────────────────────────────
class TestSageMakerContract:
    """驗證 Lambda 產生正確的 InvokeEndpoint 請求，並嚴格驗證回應 schema。"""

    def test_content_type_is_octet_stream(self) -> None:
        """ContentType 必須是 application/octet-stream。"""
        client = FakeSageMakerClient()
        run(build(client))
        assert client.calls[0]["ContentType"] == "application/octet-stream"

    def test_accept_is_json(self) -> None:
        """Accept 必須是 application/json。"""
        client = FakeSageMakerClient()
        run(build(client))
        assert client.calls[0]["Accept"] == "application/json"

    def test_body_is_raw_pcm_s16le_bytes(self) -> None:
        """Body 必須是 CanonicalAudio 的原始 PCM bytes，非 base64、非容器格式。"""
        client = FakeSageMakerClient()
        run(build(client))
        body = client.calls[0]["Body"]
        assert isinstance(body, bytes)
        assert body == AUDIO.pcm_s16le
        # 不是 base64 或 WAV/M4A header
        assert not body.startswith(b"RIFF")
        assert not body.startswith(b"\x00\x00\x00")

    def test_custom_attributes_format_is_semicolon_separated(self) -> None:
        """CustomAttributes 格式：key=value 以分號分隔。"""
        client = FakeSageMakerClient()
        run(build(client))
        attrs = client.calls[0]["CustomAttributes"]
        parts = attrs.split(";")
        assert len(parts) == 3
        kv_pairs = {p.split("=")[0]: p.split("=")[1] for p in parts}
        assert kv_pairs["language"] == "zh-TW"
        assert kv_pairs["sample_rate_hz"] == "16000"
        assert kv_pairs["channels"] == "1"

    def test_custom_attributes_with_hak_language(self) -> None:
        """客語請求的 CustomAttributes 應帶 language=hak。"""
        client = FakeSageMakerClient()
        run(build(client), language=Language.HAK)
        attrs = client.calls[0]["CustomAttributes"]
        assert "language=hak" in attrs

    def test_no_extra_invoke_endpoint_fields(self) -> None:
        """InvokeEndpoint 呼叫只包含 5 個欄位，不夾帶任何額外資訊。"""
        client = FakeSageMakerClient()
        run(build(client))
        assert set(client.calls[0]) == {
            "EndpointName",
            "ContentType",
            "Accept",
            "Body",
            "CustomAttributes",
        }

    def test_endpoint_name_matches_spec(self) -> None:
        """EndpointName 必須精確匹配 RemoteEndpointSpec。"""
        client = FakeSageMakerClient()
        run(build(client))
        assert client.calls[0]["EndpointName"] == "ai-elder-care-asr-ce"

    def test_response_text_field_is_extracted(self) -> None:
        """成功回應 {"text": "..."} 的 text 欄位被正確取出。"""
        client = FakeSageMakerClient(payload={"text": "你好世界"})
        record = run(build(client))
        assert isinstance(record.result, Transcript)
        assert record.result.text == "你好世界"

    def test_response_with_extra_fields_only_uses_text(self) -> None:
        """回應帶額外欄位時，只取 text，不猜測其他欄位。"""
        client = FakeSageMakerClient(
            payload={"text": "辨識結果", "confidence": 0.95, "segments": []}
        )
        record = run(build(client))
        assert isinstance(record.result, Transcript)
        assert record.result.text == "辨識結果"

    def test_response_text_null_is_invalid(self) -> None:
        """text 為 null 視為無效回應。"""
        client = FakeSageMakerClient(payload={"text": None})
        record = run(build(client))
        assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


class TestSageMakerTimeoutErrors:
    """驗證各種逾時類例外的分類。"""

    def test_connect_timeout_becomes_deadline_exceeded(self) -> None:
        """連線逾時也應被分類為 deadline_exceeded。"""

        class ConnectTimeoutError(Exception):
            pass

        record = run(build(FakeSageMakerClient(raise_exc=ConnectTimeoutError())))
        assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
        assert record.result.retryable is True

    def test_connection_closed_becomes_deadline_exceeded(self) -> None:
        """連線被關閉也視為逾時。"""

        class ConnectionClosedError(Exception):
            pass

        record = run(build(FakeSageMakerClient(raise_exc=ConnectionClosedError())))
        assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED

    def test_all_transient_codes_are_covered(self) -> None:
        """所有已知暫時性錯誤碼都映射為 provider_unavailable。"""
        for code in [
            "ThrottlingException",
            "ModelNotReadyException",
            "ServiceUnavailable",
            "ServiceUnavailableException",
            "InternalFailure",
            "InternalServerException",
            "ModelError",
            "TooManyRequestsException",
        ]:
            record = run(build(FakeSageMakerClient(raise_exc=ClientError(code))))
            assert record.result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE, (
                f"Expected PROVIDER_UNAVAILABLE for {code}"
            )


class TestSageMakerResponseBodyErrors:
    """驗證回應 body 讀取失敗的情境。"""

    def test_body_read_raises_exception(self) -> None:
        """Body.read() 拋例外時視為無效回應。"""

        class BrokenBody:
            def read(self):
                raise IOError("stream reset")

        client = FakeSageMakerClient()
        provider = build(client)
        # 覆蓋 invoke_endpoint 回傳壞掉的 body
        original_invoke = client.invoke_endpoint

        def broken_invoke(**kwargs):
            client.calls.append(kwargs)
            return {"Body": BrokenBody()}

        client.invoke_endpoint = broken_invoke
        record = run(provider)
        assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE

    def test_empty_body_is_invalid(self) -> None:
        """空 body 不是合法 JSON。"""
        record = run(build(FakeSageMakerClient(payload=b"")))
        assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


class TestSageMakerSecurityConstraints:
    """驗證安全限制：不記錄音訊或逐字稿。"""

    def test_error_messages_do_not_contain_audio_bytes(self) -> None:
        """錯誤訊息不得包含音訊 bytes 的任何片段。"""
        record = run(
            build(FakeSageMakerClient(raise_exc=ClientError("ValidationError")))
        )
        msg = record.result.message
        # 音訊 bytes 的 hex 不應出現在訊息中
        assert "\\x01\\x02" not in msg
        assert AUDIO.pcm_s16le[:10].hex() not in msg

    def test_error_messages_do_not_contain_transcript(self) -> None:
        """即使端點回傳了文字，錯誤路徑不應把它放進 message。"""
        # 模擬：端點回傳文字但 postflight 發現已取消
        client = FakeSageMakerClient(payload={"text": "機密逐字稿內容"})
        cancelled = CancellationSignal()

        class DelayedCancelClient(FakeSageMakerClient):
            def invoke_endpoint(self, **kwargs):
                self.calls.append(kwargs)
                # 在呼叫後觸發取消
                cancelled.trigger()
                return {"Body": io.BytesIO(json.dumps({"text": "機密逐字稿內容"}).encode())}

        provider = build(DelayedCancelClient())
        record = run(provider, cancellation=cancelled)
        # 應該回傳 cancelled（postflight 檢查）
        assert record.result.category is AsrErrorCategory.CANCELLED
        assert "機密" not in record.result.message

    def test_custom_attributes_never_contain_pii(self) -> None:
        """CustomAttributes 不得含任何 PII（elder_id、correlation_id 等）。"""
        client = FakeSageMakerClient()
        run(build(client))
        attrs = client.calls[0]["CustomAttributes"]
        for pii_token in ("elder", "eld_", "corr-", "cognito", "token", "prompt"):
            assert pii_token not in attrs.lower()
