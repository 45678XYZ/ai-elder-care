"""
AWS zh-TW Adapter Property-Based Tests。

以 Hypothesis 驗證 fake transport 邊界、deadline/cancellation precedence
與錯誤正規化，至少 100 iterations。

**Validates: Requirements 2.6, 4.1, 4.6, 4.7, 4.8, 8.8; Design Property 2**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import given, assume, note
from hypothesis import strategies as st

from src.shared.asr import (
    AsrErrorCategory,
    AwsCapabilityGate,
    AwsZhAdapter,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TransportCancelled,
    TransportDeadlineExceeded,
    TransportRequest,
    TransportUnavailable,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Hypothesis Strategies
# ─────────────────────────────────────────────────────────────────

@st.composite
def canonical_audio_st(draw: st.DrawFn) -> CanonicalAudio:
    """生成有效的 CanonicalAudio：non-empty PCM bytes (even length), 16kHz, mono, 16-bit。"""
    n_samples = draw(st.integers(min_value=1, max_value=500))
    pcm = draw(st.binary(min_size=n_samples * 2, max_size=n_samples * 2))
    duration_ms = draw(st.integers(min_value=0, max_value=60000))
    input_format = draw(st.sampled_from([InputFormat.WAV, InputFormat.M4A]))
    return CanonicalAudio(
        pcm_s16le=pcm,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=duration_ms,
        input_format=input_format,
    )


@st.composite
def correlation_context_st(draw: st.DrawFn) -> CorrelationContext:
    """生成有效的 CorrelationContext（非空白 correlation_id）。"""
    cid = draw(st.text(min_size=1, max_size=50, alphabet=st.characters(
        categories=("L", "N", "P", "S"),
        exclude_characters="\x00",
    )).filter(lambda s: s.strip() != ""))
    return CorrelationContext(correlation_id=cid)


@st.composite
def deadline_st(draw: st.DrawFn, *, expired: bool | None = None) -> Deadline:
    """
    生成 Deadline。

    expired=None → 隨機選擇；expired=True → 必定已到期；expired=False → 必定未到期。
    """
    if expired is None:
        is_expired = draw(st.booleans())
    else:
        is_expired = expired

    if is_expired:
        # clock >= expiry → expired
        expiry = draw(st.floats(min_value=0.0, max_value=1000.0))
        clock_val = draw(st.floats(min_value=expiry, max_value=expiry + 1000.0))
        return Deadline.create(expiry=expiry, clock=lambda cv=clock_val: cv)
    else:
        # clock < expiry → not expired
        clock_val = draw(st.floats(min_value=0.0, max_value=1000.0))
        expiry = draw(st.floats(min_value=clock_val + 0.001, max_value=clock_val + 10000.0))
        return Deadline.create(expiry=expiry, clock=lambda cv=clock_val: cv)


def cancellation_st(*, triggered: bool | None = None) -> st.SearchStrategy[CancellationSignal]:
    """
    生成 CancellationSignal。

    triggered=None → 隨機；triggered=True → 已觸發；triggered=False → 未觸發。
    """
    if triggered is None:
        return st.booleans().map(_make_cancellation)
    elif triggered:
        return st.just(_make_cancellation(True))
    else:
        return st.just(_make_cancellation(False))


def _make_cancellation(triggered: bool) -> CancellationSignal:
    sig = CancellationSignal()
    if triggered:
        sig.trigger()
    return sig


def _complete_gate() -> AwsCapabilityGate:
    """建立完整的 AWS capability gate（9 項全部 True）。"""
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


# ─────────────────────────────────────────────────────────────────
# Transport spies for property testing
# ─────────────────────────────────────────────────────────────────

# Fields that transport is ALLOWED to see (via TransportRequest)
_ALLOWED_TRANSPORT_FIELDS = frozenset(
    ["audio", "language", "deadline", "cancellation", "correlation_id"]
)

# Fields that MUST NOT appear in transport requests
_FORBIDDEN_CONCEPTS = frozenset([
    "source_audio", "raw_wav", "raw_m4a", "service", "region",
    "endpoint", "token", "prompt", "iam", "hf_token", "api_key",
])


@dataclass
class SpyTransport:
    """
    Spy transport 記錄收到的 TransportRequest 以供驗證。
    可配置回傳值或 raise exception。
    """

    calls: list[TransportRequest] = field(default_factory=list)
    result: str | None = "辨識結果"
    exception: Exception | None = None

    def transcribe(self, request: TransportRequest) -> str | None:
        self.calls.append(request)
        if self.exception is not None:
            raise self.exception
        return self.result


@dataclass
class PostflightCancelTransport:
    """Transport 執行過程中觸發 cancellation。"""

    calls: list[TransportRequest] = field(default_factory=list)
    cancellation: CancellationSignal | None = None

    def transcribe(self, request: TransportRequest) -> str | None:
        self.calls.append(request)
        if self.cancellation is not None:
            self.cancellation.trigger()
        return "成功結果"


# ─────────────────────────────────────────────────────────────────
# Transport result strategies
# ─────────────────────────────────────────────────────────────────

# Valid non-blank transcript text (Chinese/CJK text)
valid_transcript_text_st = st.text(
    min_size=1, max_size=200,
    alphabet=st.characters(categories=("L", "N", "P"), exclude_characters="\x00"),
).filter(lambda s: s.strip() != "")

# Blank/empty text (whitespace only or empty string)
blank_text_st = st.one_of(
    st.just(""),
    st.just("   "),
    st.just("\t\n"),
    st.text(alphabet=" \t\n\r", min_size=0, max_size=20),
)

# Unclassified exceptions (not known transport exceptions)
unclassified_exception_st = st.one_of(
    st.just(RuntimeError("unexpected error")),
    st.just(ValueError("bad data")),
    st.just(IOError("io fail")),
    st.just(TypeError("wrong type")),
    st.just(Exception("generic")),
)


# ─────────────────────────────────────────────────────────────────
# Property Tests
# ─────────────────────────────────────────────────────────────────

class TestAllowedFieldsProperty:
    """
    Property: 完整 gate 下 transport 只接收允許欄位。

    **Validates: Requirements 2.6, 4.1, 8.8; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
    )
    def test_transport_receives_only_allowed_fields(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline
    ) -> None:
        """
        For all valid inputs with complete gate, non-cancelled, non-expired:
        transport request contains exactly CanonicalAudio, "zh-TW", deadline,
        cancellation, correlation_id — nothing else.
        """
        cancellation = _make_cancellation(False)
        spy = SpyTransport(result="有效結果")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        # Transport must be called exactly once
        assert len(spy.calls) == 1
        req = spy.calls[0]

        # Verify TransportRequest fields — these are the ONLY allowed fields
        assert req.audio is audio
        assert req.language == "zh-TW"
        assert req.deadline is deadline
        assert req.cancellation is cancellation
        assert req.correlation_id == context.correlation_id

        # Verify TransportRequest only has the known fields (dataclass fields)
        request_fields = set(vars(req).keys())
        assert request_fields == _ALLOWED_TRANSPORT_FIELDS

        # Result should be Transcript (valid text)
        assert isinstance(result, Transcript)


class TestCancellationPreflightProperty:
    """
    Property: 已取消的輸入 → adapter 不呼叫 transport，回傳 cancelled。

    **Validates: Requirements 4.6, 4.7; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
    )
    def test_cancel_preflight_zero_call_gate(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline
    ) -> None:
        """
        For all inputs where cancellation is already triggered before transcribe:
        transport is never called and result is cancelled.
        """
        cancellation = _make_cancellation(True)
        spy = SpyTransport(result="should not reach")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        # Zero transport calls
        assert len(spy.calls) == 0
        # Result is cancelled
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED


class TestDeadlinePreflightProperty:
    """
    Property: 已逾期的輸入 → adapter 不呼叫 transport，回傳 deadline_exceeded。

    **Validates: Requirements 4.6, 4.8; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=True),
    )
    def test_deadline_preflight_zero_call_gate(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline
    ) -> None:
        """
        For all inputs where deadline is already expired before transcribe
        (and cancellation not triggered): transport is never called and
        result is deadline_exceeded.
        """
        cancellation = _make_cancellation(False)
        spy = SpyTransport(result="should not reach")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        # Zero transport calls
        assert len(spy.calls) == 0
        # Result is deadline_exceeded
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED


class TestPreflightPrecedenceProperty:
    """
    Property: cancellation preflight 優先於 deadline preflight。

    **Validates: Requirements 4.6, 4.7; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=True),
    )
    def test_cancel_takes_precedence_over_deadline(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline
    ) -> None:
        """
        When both cancellation triggered AND deadline expired:
        cancellation preflight wins (appears first in precedence chain).
        """
        cancellation = _make_cancellation(True)
        spy = SpyTransport(result="should not reach")
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(spy.calls) == 0
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED


class TestBlankResultNormalizationProperty:
    """
    Property: transport 回傳 blank/None → provider_invalid_response。

    **Validates: Requirements 4.8; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
        blank_result=blank_text_st,
    )
    def test_blank_transport_result_normalized(
        self, audio: CanonicalAudio, context: CorrelationContext,
        deadline: Deadline, blank_result: str,
    ) -> None:
        """
        For all blank/whitespace-only transport results:
        adapter normalizes to provider_invalid_response.
        """
        cancellation = _make_cancellation(False)
        spy = SpyTransport(result=blank_result)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(spy.calls) == 1
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_INVALID_RESPONSE

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
    )
    def test_none_transport_result_normalized(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline,
    ) -> None:
        """
        For all inputs where transport returns None:
        adapter normalizes to provider_invalid_response.
        """
        cancellation = _make_cancellation(False)
        spy = SpyTransport(result=None)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(spy.calls) == 1
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_INVALID_RESPONSE


class TestUnclassifiedExceptionProperty:
    """
    Property: transport raise 未分類 exception → provider_failure，
    且 message 不含 exception text。

    **Validates: Requirements 4.8; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
        exc=unclassified_exception_st,
    )
    def test_unclassified_exception_normalized_to_provider_failure(
        self, audio: CanonicalAudio, context: CorrelationContext,
        deadline: Deadline, exc: Exception,
    ) -> None:
        """
        For all unclassified exceptions raised by transport:
        adapter normalizes to provider_failure and does not leak exception text.
        """
        cancellation = _make_cancellation(False)
        spy = SpyTransport(exception=exc)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=spy)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(spy.calls) == 1
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.PROVIDER_FAILURE
        # Exception message must not leak into the error message
        exc_msg = str(exc)
        if exc_msg:
            assert exc_msg not in result.message


class TestPostflightCancellationProperty:
    """
    Property: 如果 cancel 在 transport 執行中觸發 → cancelled 覆蓋成功結果。

    **Validates: Requirements 4.7; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
        deadline=deadline_st(expired=False),
    )
    def test_postflight_cancel_overrides_success(
        self, audio: CanonicalAudio, context: CorrelationContext, deadline: Deadline,
    ) -> None:
        """
        For all inputs where transport succeeds but cancellation is triggered
        during transport execution: result is cancelled (postflight override).
        """
        cancellation = _make_cancellation(False)
        transport = PostflightCancelTransport(cancellation=cancellation)
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(transport.calls) == 1
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED


class TestPostflightDeadlineProperty:
    """
    Property: 如果 deadline 在 transport 執行中到期 → deadline_exceeded 覆蓋成功結果。

    **Validates: Requirements 4.8; Design Property 2**
    """

    @given(
        audio=canonical_audio_st(),
        context=correlation_context_st(),
    )
    def test_postflight_deadline_overrides_success(
        self, audio: CanonicalAudio, context: CorrelationContext,
    ) -> None:
        """
        For all inputs where transport succeeds but deadline expires
        during transport execution: result is deadline_exceeded (postflight override).
        """
        # Use mutable clock that advances past expiry during transport call
        clock_state = [0.0]
        expiry = 100.0

        def clock() -> float:
            return clock_state[0]

        deadline = Deadline.create(expiry=expiry, clock=clock)

        cancellation = _make_cancellation(False)

        @dataclass
        class AdvancingTransport:
            calls: list[TransportRequest] = field(default_factory=list)

            def transcribe(self, request: TransportRequest) -> str | None:
                self.calls.append(request)
                # Simulate: transport execution causes clock to exceed deadline
                clock_state[0] = expiry + 1.0
                return "成功結果"

        transport = AdvancingTransport()
        adapter = AwsZhAdapter(capability_gate=_complete_gate(), transport=transport)

        result = adapter.transcribe(audio, Language.ZH_TW, deadline, cancellation, context)

        assert len(transport.calls) == 1
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED
