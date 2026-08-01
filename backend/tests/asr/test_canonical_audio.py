"""
Canonical Audio fixture tests — validates canonicalizer against controlled audio fixtures.

Validates: Requirements 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 8.4, 8.5, 8.6
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.shared.asr.canonical_audio import canonicalize
from src.shared.asr.types import (
    AsrErrorCategory,
    CanonicalAudio,
    InputFormat,
    TypedAsrError,
)

# ─────────────────────────────────────────────────────────────────
# Fixture directory and manifest
# ─────────────────────────────────────────────────────────────────
FIXTURES_DIR = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    """Load fixture manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_fixture(filename: str) -> bytes:
    """Load fixture file bytes."""
    return (FIXTURES_DIR / filename).read_bytes()


# ─────────────────────────────────────────────────────────────────
# Success cases — short WAV
# ─────────────────────────────────────────────────────────────────
class TestShortValidWav:
    """Short valid WAV should produce CanonicalAudio."""

    def test_produces_canonical_audio(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)

    def test_sample_rate_16khz(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert result.sample_rate_hz == 16000

    def test_mono_channel(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert result.channels == 1

    def test_16bit_sample_width(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert result.sample_width_bits == 16

    def test_pcm_bytes_non_empty(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert len(result.pcm_s16le) > 0

    def test_input_format_preserved(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert result.input_format == InputFormat.WAV

    def test_duration_ms_positive(self) -> None:
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        assert result.duration_ms > 0


# ─────────────────────────────────────────────────────────────────
# Success cases — valid M4A
# ─────────────────────────────────────────────────────────────────
class TestValidM4a:
    """Valid M4A should produce CanonicalAudio."""

    def test_produces_canonical_audio(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)

    def test_sample_rate_16khz(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)
        assert result.sample_rate_hz == 16000

    def test_mono_channel(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)
        assert result.channels == 1

    def test_16bit_sample_width(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)
        assert result.sample_width_bits == 16

    def test_pcm_bytes_non_empty(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)
        assert len(result.pcm_s16le) > 0

    def test_input_format_preserved(self) -> None:
        audio_bytes = _load_fixture("valid.m4a")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, CanonicalAudio)
        assert result.input_format == InputFormat.M4A


# ─────────────────────────────────────────────────────────────────
# Error cases — empty bytes
# ─────────────────────────────────────────────────────────────────
class TestEmptyBytes:
    """Empty audio bytes should return invalid_audio."""

    def test_empty_bytes_returns_invalid_audio(self) -> None:
        result = canonicalize(b"", InputFormat.WAV)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO

    def test_empty_bytes_m4a_returns_invalid_audio(self) -> None:
        result = canonicalize(b"", InputFormat.M4A)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO


# ─────────────────────────────────────────────────────────────────
# Error cases — corrupt audio
# ─────────────────────────────────────────────────────────────────
class TestCorruptAudio:
    """Corrupt audio should return invalid_audio."""

    def test_corrupt_wav_returns_invalid_audio(self) -> None:
        audio_bytes = _load_fixture("corrupt.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO

    def test_random_bytes_as_wav_returns_invalid_audio(self) -> None:
        result = canonicalize(b"\x00\xff" * 50, InputFormat.WAV)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO

    def test_random_bytes_as_m4a_returns_invalid_audio(self) -> None:
        result = canonicalize(b"\x00\xff" * 50, InputFormat.M4A)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO


# ─────────────────────────────────────────────────────────────────
# Error cases — format/content mismatch
# ─────────────────────────────────────────────────────────────────
class TestFormatMismatch:
    """Format/content mismatch should return invalid_audio."""

    def test_wav_content_declared_as_m4a(self) -> None:
        """WAV file declared as M4A → invalid_audio (no ftyp box in WAV)."""
        audio_bytes = _load_fixture("mismatch_wav_as_m4a.wav")
        result = canonicalize(audio_bytes, InputFormat.M4A)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO

    def test_m4a_content_declared_as_wav(self) -> None:
        """M4A-like content declared as WAV → invalid_audio (no RIFF/WAVE header)."""
        audio_bytes = _load_fixture("mismatch_m4a_as_wav.m4a")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO


# ─────────────────────────────────────────────────────────────────
# Duration boundary tests
# ─────────────────────────────────────────────────────────────────
class TestDurationBoundary:
    """Duration boundary precision tests."""

    def test_59999ms_accepted(self) -> None:
        """59.999s (959984 frames at 16kHz) MUST be accepted."""
        audio_bytes = _load_fixture("boundary_59999ms.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio), (
            f"Expected CanonicalAudio for 59.999s, got {result}"
        )
        assert result.duration_ms == 59999

    def test_60000ms_accepted(self) -> None:
        """60.000s (960000 frames at 16kHz) MUST be accepted."""
        audio_bytes = _load_fixture("boundary_60000ms.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio), (
            f"Expected CanonicalAudio for 60.000s, got {result}"
        )
        assert result.duration_ms == 60000

    def test_60001ms_rejected(self) -> None:
        """60.001s (960016 frames at 16kHz) MUST return audio_duration_exceeded."""
        audio_bytes = _load_fixture("boundary_60001ms.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError for 60.001s, got {result}"
        )
        assert result.category == AsrErrorCategory.AUDIO_DURATION_EXCEEDED


# ─────────────────────────────────────────────────────────────────
# Canonical Audio immutability — source bytes isolation
# ─────────────────────────────────────────────────────────────────
class TestSourceBytesIsolation:
    """Canonical Audio must not expose source bytes to providers."""

    def test_pcm_differs_from_source(self) -> None:
        """CanonicalAudio.pcm_s16le should not be identical to source WAV bytes."""
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        # PCM content should differ from the WAV container
        assert result.pcm_s16le != audio_bytes

    def test_canonical_audio_is_frozen(self) -> None:
        """CanonicalAudio dataclass is frozen — cannot mutate."""
        audio_bytes = _load_fixture("short_valid.wav")
        result = canonicalize(audio_bytes, InputFormat.WAV)
        assert isinstance(result, CanonicalAudio)
        with pytest.raises(Exception):
            result.pcm_s16le = b"modified"  # type: ignore[misc]
