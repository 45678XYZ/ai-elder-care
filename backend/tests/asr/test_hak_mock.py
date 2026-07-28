"""
Tests for HakMockProvider — hak_mock.py。

驗證：
- Deterministic output（same input → same output）
- Non-blank Unicode transcript
- Output 不依據 audio content（different audio → same output）
- No model/network/cloud calls（verified by network block fixture in conftest）
"""
from __future__ import annotations

from src.shared.asr.hak_mock import HakMockProvider
from src.shared.asr.types import (
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
)


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────
def _make_canonical_audio(content: bytes | None = None) -> CanonicalAudio:
    """Build a CanonicalAudio with optional content bytes."""
    pcm = content if content is not None else b"\x00\x00" * 160
    # Ensure even byte count for 16-bit samples
    if len(pcm) == 0:
        pcm = b"\x00\x00"
    return CanonicalAudio(
        pcm_s16le=pcm,
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


def _make_context(cid: str = "hak-test") -> CorrelationContext:
    return CorrelationContext(correlation_id=cid)


# ─────────────────────────────────────────────────────────────────
# Tests: Deterministic output
# ─────────────────────────────────────────────────────────────────
class TestHakMockDeterministic:
    """Same input → same output, every time."""

    def test_same_input_same_output(self) -> None:
        provider = HakMockProvider()
        audio = _make_canonical_audio()
        deadline = _make_deadline()
        cancellation = _make_cancellation()
        context = _make_context()

        result1 = provider.transcribe(audio, Language.HAK, deadline, cancellation, context)
        result2 = provider.transcribe(audio, Language.HAK, deadline, cancellation, context)

        assert result1 == result2

    def test_multiple_calls_always_same(self) -> None:
        provider = HakMockProvider()
        audio = _make_canonical_audio()
        deadline = _make_deadline()
        cancellation = _make_cancellation()
        context = _make_context()

        results = [
            provider.transcribe(audio, Language.HAK, deadline, cancellation, context)
            for _ in range(10)
        ]

        assert all(r == results[0] for r in results)


# ─────────────────────────────────────────────────────────────────
# Tests: Non-blank Unicode transcript
# ─────────────────────────────────────────────────────────────────
class TestHakMockTranscript:
    """Output is a non-blank Unicode transcript."""

    def test_returns_transcript_type(self) -> None:
        provider = HakMockProvider()
        result = provider.transcribe(
            _make_canonical_audio(),
            Language.HAK,
            _make_deadline(),
            _make_cancellation(),
            _make_context(),
        )
        assert isinstance(result, Transcript)

    def test_transcript_text_non_blank(self) -> None:
        provider = HakMockProvider()
        result = provider.transcribe(
            _make_canonical_audio(),
            Language.HAK,
            _make_deadline(),
            _make_cancellation(),
            _make_context(),
        )
        assert isinstance(result, Transcript)
        assert result.text.strip() != ""

    def test_transcript_text_is_unicode(self) -> None:
        provider = HakMockProvider()
        result = provider.transcribe(
            _make_canonical_audio(),
            Language.HAK,
            _make_deadline(),
            _make_cancellation(),
            _make_context(),
        )
        assert isinstance(result, Transcript)
        assert isinstance(result.text, str)
        # Verify contains non-ASCII (Chinese characters)
        assert any(ord(c) > 127 for c in result.text)


# ─────────────────────────────────────────────────────────────────
# Tests: Output does NOT depend on audio content
# ─────────────────────────────────────────────────────────────────
class TestHakMockAudioIndependent:
    """Different audio content → same output."""

    def test_different_audio_same_output(self) -> None:
        provider = HakMockProvider()
        audio_silence = _make_canonical_audio(b"\x00\x00" * 160)
        audio_noise = _make_canonical_audio(b"\xff\x7f" * 160)
        deadline = _make_deadline()
        cancellation = _make_cancellation()
        context = _make_context()

        result1 = provider.transcribe(
            audio_silence, Language.HAK, deadline, cancellation, context
        )
        result2 = provider.transcribe(
            audio_noise, Language.HAK, deadline, cancellation, context
        )

        assert result1 == result2

    def test_different_duration_same_output(self) -> None:
        provider = HakMockProvider()
        audio_short = CanonicalAudio(
            pcm_s16le=b"\x00\x00" * 16,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bits=16,
            duration_ms=1,
            input_format=InputFormat.WAV,
        )
        audio_long = CanonicalAudio(
            pcm_s16le=b"\x00\x00" * 16000,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bits=16,
            duration_ms=1000,
            input_format=InputFormat.M4A,
        )
        deadline = _make_deadline()
        cancellation = _make_cancellation()
        context = _make_context()

        result1 = provider.transcribe(
            audio_short, Language.HAK, deadline, cancellation, context
        )
        result2 = provider.transcribe(
            audio_long, Language.HAK, deadline, cancellation, context
        )

        assert result1 == result2


# ─────────────────────────────────────────────────────────────────
# Tests: No network calls
# ─────────────────────────────────────────────────────────────────
class TestHakMockNoNetwork:
    """No model/network/cloud calls — verified by conftest network block fixture (autouse)."""

    def test_transcribe_succeeds_with_network_blocked(self) -> None:
        """
        Network is blocked by conftest autouse fixture.
        If HakMockProvider made any network call, this would raise OSError.
        """
        provider = HakMockProvider()
        result = provider.transcribe(
            _make_canonical_audio(),
            Language.HAK,
            _make_deadline(),
            _make_cancellation(),
            _make_context(),
        )
        # If we reach here, no network call was made
        assert isinstance(result, Transcript)
