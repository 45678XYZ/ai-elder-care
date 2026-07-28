"""
Tests for ASR Provider Protocol 與 TransportRequest — providers.py。

驗證：
- TransportRequest 只接受 CanonicalAudio（不接受 raw bytes）
- TransportRequest language 固定為 "zh-TW"
- TransportRequest correlation_id 必須是非空白字串
- HakMockProvider 結構性滿足 AsrProvider protocol
"""
from __future__ import annotations

import pytest

from src.shared.asr.providers import AsrProvider, TransportRequest
from src.shared.asr.hak_mock import HakMockProvider
from src.shared.asr.types import (
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
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
# Tests: TransportRequest accepts only CanonicalAudio
# ─────────────────────────────────────────────────────────────────
class TestTransportRequestCanonicalOnly:
    """TransportRequest.audio 必須是 CanonicalAudio，不接受 raw bytes。"""

    def test_valid_canonical_audio_accepted(self) -> None:
        req = TransportRequest(
            audio=_make_canonical_audio(),
            language="zh-TW",
            deadline=_make_deadline(),
            cancellation=_make_cancellation(),
            correlation_id="corr-001",
        )
        assert isinstance(req.audio, CanonicalAudio)

    def test_raw_bytes_rejected(self) -> None:
        with pytest.raises(TypeError, match="must be CanonicalAudio"):
            TransportRequest(
                audio=b"\x00\x00" * 160,  # type: ignore
                language="zh-TW",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="corr-002",
            )

    def test_none_audio_rejected(self) -> None:
        with pytest.raises(TypeError, match="must be CanonicalAudio"):
            TransportRequest(
                audio=None,  # type: ignore
                language="zh-TW",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="corr-003",
            )


# ─────────────────────────────────────────────────────────────────
# Tests: TransportRequest language validation
# ─────────────────────────────────────────────────────────────────
class TestTransportRequestLanguage:
    """TransportRequest.language 固定為 'zh-TW'。"""

    def test_zh_tw_accepted(self) -> None:
        req = TransportRequest(
            audio=_make_canonical_audio(),
            language="zh-TW",
            deadline=_make_deadline(),
            cancellation=_make_cancellation(),
            correlation_id="corr-004",
        )
        assert req.language == "zh-TW"

    def test_non_zh_tw_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be 'zh-TW'"):
            TransportRequest(
                audio=_make_canonical_audio(),
                language="hak",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="corr-005",
            )

    def test_empty_language_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be 'zh-TW'"):
            TransportRequest(
                audio=_make_canonical_audio(),
                language="",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="corr-006",
            )


# ─────────────────────────────────────────────────────────────────
# Tests: TransportRequest correlation_id validation
# ─────────────────────────────────────────────────────────────────
class TestTransportRequestCorrelationId:
    """TransportRequest.correlation_id 必須非空白。"""

    def test_valid_correlation_id_accepted(self) -> None:
        req = TransportRequest(
            audio=_make_canonical_audio(),
            language="zh-TW",
            deadline=_make_deadline(),
            cancellation=_make_cancellation(),
            correlation_id="valid-id-123",
        )
        assert req.correlation_id == "valid-id-123"

    def test_empty_correlation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank string"):
            TransportRequest(
                audio=_make_canonical_audio(),
                language="zh-TW",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="",
            )

    def test_whitespace_only_correlation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank string"):
            TransportRequest(
                audio=_make_canonical_audio(),
                language="zh-TW",
                deadline=_make_deadline(),
                cancellation=_make_cancellation(),
                correlation_id="   ",
            )


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
        # Should not raise
        result = provider.transcribe(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline(),
            cancellation=_make_cancellation(),
            context=CorrelationContext(correlation_id="proto-test"),
        )
        # Result should be Transcript or TypedAsrError
        from src.shared.asr.types import Transcript, TypedAsrError

        assert isinstance(result, (Transcript, TypedAsrError))

    def test_isinstance_check_via_runtime_protocol(self) -> None:
        """Structural protocol check (runtime_checkable would require Protocol decoration)."""
        # Verify all required attributes/methods exist
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
