"""
AwsZhAdapter 單元測試。

驗證 capability gate、pre/postflight deadline-cancel checks 與結果正規化。
涵蓋 Requirements 4.1–4.9。
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.shared.asr import (
    AsrErrorCategory,
    AwsCapabilityGate,
    AwsZhAdapter,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TransportCancelled,
    TransportDeadlineExceeded,
    TransportRequest,
    TransportUnavailable,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────
def _make_audio() -> CanonicalAudio:
    """建立有效的 CanonicalAudio。"""
    from src.shared.asr.types import InputFormat

    # 最小的合法 PCM：2 bytes (1 sample, 16-bit, 1 channel)
    return CanonicalAudio(
        pcm_s16le=b"\x00\x01" * 16,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=1,
        input_format=InputFormat.WAV,
    )


def _make_context() -> CorrelationContext:
    return CorrelationContext(correlation_id="test-corr-001")


def _make_deadline(expired: bool = False) -> Deadline:
    """建立 Deadline。expired=True 表示已到期。"""
    if expired:
        # 設定 expiry 為過去時刻
        return Deadline.create(expiry=0.0, clock=lambda: 1.0)
    else:
        # 設定 expiry 為未來時刻
        return Deadline.create(expiry=999999.0, clock=lambda: 0.0)


def _make_cancellation(triggered: bool = False) -> CancellationSignal:
    sig = CancellationSignal()
    if triggered:
        sig.trigger()
    return sig


def _complete_gate() -> AwsCapabilityGate:
    """建立完整（所有 9 項皆 True）的 gate。"""
    return AwsCapabilityGate(
        region_zh_tw_support=True,
        service_input_output_mode=True,
        canonical_pcm_compatibility=True,
        timeout_behavior=True,
        cancellation_behavior=True,
        iam_permissions=True,
        s3_necessity=True,
        s3_result_handling=True,
        s3_cleanup_requirement=True,
        approval_record_ref="ADR-001",
    )


def _incomplete_gate() -> AwsCapabilityGate:
    """建立不完整的 gate（至少一項為 False）。"""
    return AwsCapabilityGate.default_incomplete()


class _CountingTransport:
    """計數 transport：紀錄呼叫次數，可配置回傳值或 raise。"""

    def __init__(
        self,
        result: str | None = "測試結果",
        raise_on_call: Exception | None = None,
    ) -> None:
        self.call_count = 0
        self._result = result
        self._raise_on_call = raise_on_call

    def transcribe(self, request: TransportRequest) -> str | None:
        self.call_count += 1
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._result


class _PostflightCancelTransport:
    """Transport 正常回傳，但回傳後 cancellation 被觸發。"""

    def __init__(self, cancellation: CancellationSignal) -> None:
        self._cancellation = cancellation

    def transcribe(self, request: TransportRequest) -> str | None:
        # Simulate: transport completes, then cancellation triggers
        self._cancellation.trigger()
        return "結果"


class _PostflightDeadlineTransport:
    """Transport 正常回傳，但回傳後 deadline 到期。"""

    def __init__(self) -> None:
        self._call_count = 0
        self._clock_value = 0.0

    @property
    def call_count(self) -> int:
        return self._call_count

    def set_clock(self, value: float) -> None:
        self._clock_value = value

    def transcribe(self, request: TransportRequest) -> str | None:
        self._call_count += 1
        # After transport returns, clock advances past deadline
        self.set_clock(999999.0)
        return "結果"


# ─────────────────────────────────────────────────────────────────
# Tests: Capability Gate
# ─────────────────────────────────────────────────────────────────
class TestCapabilityGate:
    """Capability gate 不完整 → route_not_approved，zero transport calls。"""

    def test_incomplete_gate_returns_route_not_approved(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_incomplete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED
        assert result.retryable is False
        assert transport.call_count == 0

    def test_complete_gate_allows_transport(self) -> None:
        transport = _CountingTransport(result="你好")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, Transcript)
        assert result.text == "你好"
        assert transport.call_count == 1

    def test_single_missing_item_fails_gate(self) -> None:
        """即使只缺一項也不通過。"""
        gate = AwsCapabilityGate(
            region_zh_tw_support=True,
            service_input_output_mode=True,
            canonical_pcm_compatibility=True,
            timeout_behavior=True,
            cancellation_behavior=True,
            iam_permissions=True,
            s3_necessity=True,
            s3_result_handling=True,
            s3_cleanup_requirement=False,  # 只缺一項
        )
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=gate, transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED
        assert transport.call_count == 0


# ─────────────────────────────────────────────────────────────────
# Tests: Preflight Cancellation
# ─────────────────────────────────────────────────────────────────
class TestPreflightCancellation:
    """Cancellation preflight 已觸發 → cancelled，zero transport calls。"""

    def test_cancelled_preflight_returns_cancelled(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)
        cancellation = _make_cancellation(triggered=True)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), cancellation, _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
        assert result.retryable is False
        assert transport.call_count == 0


# ─────────────────────────────────────────────────────────────────
# Tests: Preflight Deadline
# ─────────────────────────────────────────────────────────────────
class TestPreflightDeadline:
    """Deadline preflight 已到期 → deadline_exceeded，zero transport calls。"""

    def test_expired_deadline_preflight_returns_deadline_exceeded(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(expired=True), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED
        assert result.retryable is True
        assert transport.call_count == 0


# ─────────────────────────────────────────────────────────────────
# Tests: Postflight Cancellation
# ─────────────────────────────────────────────────────────────────
class TestPostflightCancellation:
    """Transport 完成後 cancellation 觸發 → cancelled overrides success。"""

    def test_postflight_cancel_overrides_success(self) -> None:
        cancellation = _make_cancellation()
        transport = _PostflightCancelTransport(cancellation)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), cancellation, _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
        assert result.retryable is False


# ─────────────────────────────────────────────────────────────────
# Tests: Postflight Deadline
# ─────────────────────────────────────────────────────────────────
class TestPostflightDeadline:
    """Transport 完成後 deadline 到期 → deadline_exceeded overrides success。"""

    def test_postflight_deadline_overrides_success(self) -> None:
        helper = _PostflightDeadlineTransport()
        # Clock: 在 transport 呼叫前 clock=0，transport 回傳後 clock=999999
        clock_value = [0.0]

        def clock() -> float:
            return clock_value[0]

        deadline = Deadline.create(expiry=100.0, clock=clock)

        class _AdvancingTransport:
            call_count = 0

            def transcribe(self, request: TransportRequest) -> str | None:
                self.call_count += 1
                # 模擬：transport 耗時，完成後 clock 超過 deadline
                clock_value[0] = 200.0
                return "結果"

        transport = _AdvancingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, deadline, _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED
        assert result.retryable is True
        assert transport.call_count == 1


# ─────────────────────────────────────────────────────────────────
# Tests: Transport Known Errors
# ─────────────────────────────────────────────────────────────────
class TestTransportKnownErrors:
    """Transport raise known exceptions → 正確對應 category。"""

    def test_transport_cancelled_exception(self) -> None:
        transport = _CountingTransport(raise_on_call=TransportCancelled())
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
        assert result.retryable is False

    def test_transport_deadline_exceeded_exception(self) -> None:
        transport = _CountingTransport(raise_on_call=TransportDeadlineExceeded())
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED
        assert result.retryable is True

    def test_transport_unavailable_exception(self) -> None:
        transport = _CountingTransport(raise_on_call=TransportUnavailable())
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_UNAVAILABLE
        assert result.retryable is True


# ─────────────────────────────────────────────────────────────────
# Tests: Unclassified Exceptions
# ─────────────────────────────────────────────────────────────────
class TestUnclassifiedExceptions:
    """未分類 exception → provider_failure，不外洩 exception text。"""

    def test_unclassified_exception_returns_provider_failure(self) -> None:
        transport = _CountingTransport(raise_on_call=RuntimeError("SECRET_INTERNAL_ERROR"))
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_FAILURE
        assert result.retryable is True
        # 確保不洩漏 exception text
        assert "SECRET_INTERNAL_ERROR" not in result.message

    def test_keyboard_interrupt_not_caught(self) -> None:
        """KeyboardInterrupt 等 BaseException 也被 catch — 根據設計 catch all Exception。"""
        # BaseException 不在 Exception 範圍內，不應被 catch
        transport = _CountingTransport(raise_on_call=ValueError("normal error"))
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_FAILURE


# ─────────────────────────────────────────────────────────────────
# Tests: Result Normalization
# ─────────────────────────────────────────────────────────────────
class TestResultNormalization:
    """Transport 結果正規化。"""

    def test_valid_text_returns_transcript(self) -> None:
        transport = _CountingTransport(result="  你好世界  ")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, Transcript)
        assert result.text == "你好世界"  # Unicode trimmed

    def test_none_returns_provider_invalid_response(self) -> None:
        transport = _CountingTransport(result=None)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_INVALID_RESPONSE
        assert result.retryable is False

    def test_blank_text_returns_provider_invalid_response(self) -> None:
        transport = _CountingTransport(result="   ")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_INVALID_RESPONSE
        assert result.retryable is False

    def test_empty_string_returns_provider_invalid_response(self) -> None:
        transport = _CountingTransport(result="")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_INVALID_RESPONSE
        assert result.retryable is False


# ─────────────────────────────────────────────────────────────────
# Tests: Call count invariant
# ─────────────────────────────────────────────────────────────────
class TestCallCount:
    """一次合格 invocation 最多呼叫 transport 一次。"""

    def test_successful_invocation_calls_transport_once(self) -> None:
        transport = _CountingTransport(result="結果")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert transport.call_count == 1

    def test_gate_incomplete_zero_calls(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_incomplete_gate(), transport=transport)

        adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert transport.call_count == 0

    def test_cancel_preflight_zero_calls(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(triggered=True), _make_context()
        )

        assert transport.call_count == 0

    def test_deadline_preflight_zero_calls(self) -> None:
        transport = _CountingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(expired=True), _make_cancellation(), _make_context()
        )

        assert transport.call_count == 0


# ─────────────────────────────────────────────────────────────────
# Tests: No transport → route_not_approved
# ─────────────────────────────────────────────────────────────────
class TestNoTransport:
    """完整 gate 但無 transport → route_not_approved。"""

    def test_no_transport_with_complete_gate(self) -> None:
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=None)

        result = adapter.transcribe(
            _make_audio(), Language.ZH_TW, _make_deadline(), _make_cancellation(), _make_context()
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED
