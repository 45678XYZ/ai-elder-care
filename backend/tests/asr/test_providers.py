"""
Tests for ASR Provider Protocol — providers.py。

驗證：
- HakMockProvider 結構性滿足 AsrProvider protocol
- AttemptRecord 驗證邏輯
"""
from __future__ import annotations

import pytest

from src.shared.asr.providers import AsrProvider, AttemptRecord
from src.shared.asr.hak_mock import HakMockProvider
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


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────
def _make_canonical_audio() -> CanonicalAudio:
    """Minimal valid CanonicalAudio."""
    return CanonicalAudio(
        pcm_s16le=b"\x00\x00" * 160,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=10,
        input_format=InputFormat.WAV,
    )


def _make_deadline() -> Deadline:
    return Deadline.create(expiry=999.0, clock=lambda: 0.0)


def _make_cancellation() -> CancellationSignal:
    return CancellationSignal()


# ─────────────────────────────────────────────────────────────────
# Tests: HakMockProvider satisfies AsrProvider protocol
# ─────────────────────────────────────────────────────────────────
class TestHakMockProviderProtocol:
    """HakMockProvider 結構性滿足 AsrProvider protocol。"""

    def test_has_provider_id(self) -> None:
        provider = HakMockProvider()
        assert hasattr(provider, "provider_id")
        assert provider.provider_id == "hak_mock"

    def test_has_transcribe_method(self) -> None:
        provider = HakMockProvider()
        assert callable(getattr(provider, "transcribe", None))

    def test_transcribe_signature_compatible(self) -> None:
        """HakMockProvider.transcribe 接受與 AsrProvider 協定相同的參數。"""
        provider = HakMockProvider()
        result = provider.transcribe(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline(),
            cancellation=_make_cancellation(),
            context=CorrelationContext(correlation_id="proto-test"),
        )
        assert isinstance(result, (Transcript, TypedAsrError))

    def test_isinstance_check_via_runtime_protocol(self) -> None:
        """Structural protocol check — 確認所有必要屬性/方法存在。"""
        provider = HakMockProvider()
        assert isinstance(provider.provider_id, str)
        import inspect
        sig = inspect.signature(provider.transcribe)
        params = list(sig.parameters.keys())
        assert "audio" in params
        assert "language" in params
        assert "deadline" in params
        assert "cancellation" in params
        assert "context" in params


# ─────────────────────────────────────────────────────────────────
# Tests: AttemptRecord validation
# ─────────────────────────────────────────────────────────────────
class TestAttemptRecord:
    """AttemptRecord 驗證邏輯。"""

    def test_valid_success_record(self) -> None:
        record = AttemptRecord(
            provider_id="ce_remote",
            result=Transcript(text="成功"),
            admitted=True,
            queue_wait_ms=10,
        )
        assert record.admitted is True

    def test_valid_failure_record(self) -> None:
        record = AttemptRecord(
            provider_id="ce_remote",
            result=TypedAsrError(
                category=AsrErrorCategory.PROVIDER_FAILURE,
                message="err", retryable=True,
            ),
            admitted=True,
            queue_wait_ms=0,
        )
        assert record.admitted is True

    def test_transcript_without_admission_rejected(self) -> None:
        """未放行卻成功是不可能的組合。"""
        with pytest.raises(ValueError, match="without admission"):
            AttemptRecord(
                provider_id="ce_remote",
                result=Transcript(text="不可能"),
                admitted=False,
                queue_wait_ms=0,
            )

    def test_blank_provider_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            AttemptRecord(
                provider_id="  ",
                result=Transcript(text="x"),
                admitted=True,
                queue_wait_ms=0,
            )

    def test_negative_queue_wait_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            AttemptRecord(
                provider_id="x",
                result=Transcript(text="x"),
                admitted=True,
                queue_wait_ms=-1,
            )
