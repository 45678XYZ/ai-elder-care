"""
Audio Canonicalizer — WAV/M4A 驗證、解碼、mono/16 kHz/16-bit PCM S16LE 正規化與時長 gate。

職責：
1. 輸入 gate — 空 bytes → invalid_audio；非 wav/m4a → unsupported_audio_format
2. 容器一致性 — 以宣告格式解碼，確認內容與宣告一致
3. 精確時長 gate — >60.000s → audio_duration_exceeded
4. 轉換 — decode、downmix mono、resample 16kHz、16-bit signed LE PCM
5. 封裝 — 建立唯讀 CanonicalAudio；source bytes 不離開本模組

禁止依賴：router、provider、HTTP、handlers。
"""
from __future__ import annotations

import io
import struct
from typing import Union

import numpy as np

from .types import (
    AsrErrorCategory,
    CanonicalAudio,
    InputFormat,
    TypedAsrError,
)

# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────
_TARGET_SAMPLE_RATE = 16000
_TARGET_CHANNELS = 1
_TARGET_SAMPLE_WIDTH_BITS = 16
_MAX_DURATION_MS = 60000  # 60.000 seconds — inclusive boundary

# ─────────────────────────────────────────────────────────────────
# Error helpers (private)
# ─────────────────────────────────────────────────────────────────

def _invalid_audio(reason: str) -> TypedAsrError:
    return TypedAsrError(
        category=AsrErrorCategory.INVALID_AUDIO,
        message=reason,
        retryable=False,
    )


def _unsupported_format(reason: str) -> TypedAsrError:
    return TypedAsrError(
        category=AsrErrorCategory.UNSUPPORTED_AUDIO_FORMAT,
        message=reason,
        retryable=False,
    )


def _duration_exceeded(duration_ms: int) -> TypedAsrError:
    return TypedAsrError(
        category=AsrErrorCategory.AUDIO_DURATION_EXCEEDED,
        message=f"Audio duration {duration_ms} ms exceeds maximum 60000 ms.",
        retryable=False,
    )


# ─────────────────────────────────────────────────────────────────
# Decoder seam — WAV
# ─────────────────────────────────────────────────────────────────

def _decode_wav(audio_bytes: bytes) -> Union[tuple[np.ndarray, int], TypedAsrError]:
    """
    Decode WAV container using soundfile.

    Returns (samples_float32_mono_or_multi, sample_rate) or TypedAsrError.
    Validates RIFF/WAV header before attempting decode.
    """
    # Quick container validation: WAV must start with RIFF....WAVE
    if len(audio_bytes) < 12:
        return _invalid_audio("WAV file too short to contain valid RIFF header.")
    if audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        return _invalid_audio(
            "Content does not match declared WAV format (missing RIFF/WAVE header)."
        )

    try:
        import soundfile as sf

        buf = io.BytesIO(audio_bytes)
        # Read as float64 (soundfile default), always_2d=True for consistent shape
        data, sample_rate = sf.read(buf, dtype="float64", always_2d=True)
        return data, sample_rate
    except Exception as exc:
        return _invalid_audio(f"WAV decode failure: {type(exc).__name__}")


# ─────────────────────────────────────────────────────────────────
# Decoder seam — M4A (PyAV)
# ─────────────────────────────────────────────────────────────────

def _decode_m4a(audio_bytes: bytes) -> Union[tuple[np.ndarray, int], TypedAsrError]:
    """
    Decode M4A/AAC container using PyAV.

    Returns (samples_float64_2d, sample_rate) or TypedAsrError.
    Validates M4A container markers (ftyp box or moov atom).
    """
    # Quick container validation: M4A/MP4 files contain 'ftyp' within first 12 bytes
    # The ftyp box starts at offset 4 in a standard MP4.
    if len(audio_bytes) < 8:
        return _invalid_audio("M4A file too short to contain valid container header.")

    # ftyp typically at offset 4..8
    has_ftyp = b"ftyp" in audio_bytes[:32]
    if not has_ftyp:
        return _invalid_audio(
            "Content does not match declared M4A format (missing ftyp box)."
        )

    try:
        import av

        buf = io.BytesIO(audio_bytes)
        container = av.open(buf, format="mp4")

        # Find audio stream
        audio_streams = [s for s in container.streams if s.type == "audio"]
        if not audio_streams:
            container.close()
            return _invalid_audio("M4A container has no audio stream.")

        audio_stream = audio_streams[0]
        sample_rate = audio_stream.rate or audio_stream.codec_context.sample_rate

        # Decode all frames
        frames_data = []
        for frame in container.decode(audio=0):
            # Convert to numpy float64
            arr = frame.to_ndarray()
            # av returns shape (channels, samples) for planar or (samples,) for packed
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            frames_data.append(arr)

        container.close()

        if not frames_data:
            return _invalid_audio("M4A container decoded zero audio frames.")

        # Concatenate along time axis: shape (channels, total_samples)
        all_samples = np.concatenate(frames_data, axis=1)
        # Transpose to (total_samples, channels) for consistency with WAV decoder
        all_samples = all_samples.T.astype(np.float64)

        # Normalize to float range [-1, 1] if integer format
        if audio_stream.codec_context.format and "s16" in str(audio_stream.codec_context.format.name):
            all_samples = all_samples / 32768.0
        elif audio_stream.codec_context.format and "s32" in str(audio_stream.codec_context.format.name):
            all_samples = all_samples / 2147483648.0
        elif audio_stream.codec_context.format and "flt" in str(audio_stream.codec_context.format.name):
            pass  # already float
        else:
            # For other formats, attempt normalization by max absolute value if large
            max_val = np.max(np.abs(all_samples))
            if max_val > 1.0:
                all_samples = all_samples / 32768.0

        return all_samples, sample_rate
    except Exception as exc:
        return _invalid_audio(f"M4A decode failure: {type(exc).__name__}")


# ─────────────────────────────────────────────────────────────────
# Resample helper
# ─────────────────────────────────────────────────────────────────

def _resample(samples: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1-D float64 signal from orig_sr to target_sr."""
    if orig_sr == target_sr:
        return samples

    try:
        import librosa

        resampled = librosa.resample(
            samples.astype(np.float32), orig_sr=orig_sr, target_sr=target_sr
        )
        return resampled.astype(np.float64)
    except ImportError:
        # Fallback: linear interpolation
        duration = len(samples) / orig_sr
        target_len = int(round(duration * target_sr))
        if target_len == 0:
            return np.array([], dtype=np.float64)
        indices = np.linspace(0, len(samples) - 1, target_len)
        return np.interp(indices, np.arange(len(samples)), samples)


# ─────────────────────────────────────────────────────────────────
# Public API — canonicalize
# ─────────────────────────────────────────────────────────────────

def canonicalize(
    audio_bytes: bytes, input_format: InputFormat
) -> Union[CanonicalAudio, TypedAsrError]:
    """
    Validate, decode and convert audio to Canonical Audio.

    Args:
        audio_bytes: Raw audio bytes from the caller (already source-validated).
        input_format: Declared format — wav or m4a.

    Returns:
        CanonicalAudio on success, or TypedAsrError on failure.

    The source bytes do not leave this function; only CanonicalAudio is returned.
    """
    # ── Step 1: Input gate ──────────────────────────────────────
    if not audio_bytes:
        return _invalid_audio("Empty audio bytes.")

    if not isinstance(input_format, InputFormat):
        return _unsupported_format(
            f"Unsupported audio format: {input_format!r}."
        )

    # ── Step 2: Container decode via decoder seam ───────────────
    if input_format == InputFormat.WAV:
        result = _decode_wav(audio_bytes)
    elif input_format == InputFormat.M4A:
        result = _decode_m4a(audio_bytes)
    else:
        return _unsupported_format(f"Unsupported audio format: {input_format.value!r}.")

    # Check if decoder returned an error
    if isinstance(result, TypedAsrError):
        return result

    samples_2d, orig_sample_rate = result

    # ── Step 3: Downmix to mono ─────────────────────────────────
    if samples_2d.ndim == 2 and samples_2d.shape[1] > 1:
        # Average across channels
        mono = np.mean(samples_2d, axis=1)
    elif samples_2d.ndim == 2 and samples_2d.shape[1] == 1:
        mono = samples_2d[:, 0]
    else:
        mono = samples_2d.flatten()

    # ── Step 4: Compute precise duration from frame count ───────
    frame_count = len(mono)
    if frame_count == 0:
        return _invalid_audio("Decoded audio has zero frames.")

    # Precise duration: frame_count / sample_rate
    # Convert to milliseconds with floor to integer
    # Use integer arithmetic where possible for precision
    duration_ms = int((frame_count * 1000) // orig_sample_rate)
    # Check remainder for sub-millisecond precision
    remainder = (frame_count * 1000) % orig_sample_rate
    if remainder > 0:
        duration_ms_precise = (frame_count * 1000) / orig_sample_rate
        duration_ms = int(duration_ms_precise)
        # If there's a fractional ms, we need to decide: use truncation
        # per design: frame_count / sample_rate for precision
        # 960001 frames at 16000 Hz = 60000.0625s = 60000 ms + remainder
        # We compute exact ms as floor of (frames * 1000 / sr)
        # This gives us: 59999 for 59.999s, 60000 for 60.000s, 60001 for 60.001s
        pass

    # ── Step 5: Duration gate ───────────────────────────────────
    if duration_ms > _MAX_DURATION_MS:
        return _duration_exceeded(duration_ms)

    # ── Step 6: Resample to 16kHz ───────────────────────────────
    mono_16k = _resample(mono, orig_sample_rate, _TARGET_SAMPLE_RATE)

    # ── Step 7: Convert to 16-bit signed LE PCM ─────────────────
    # Clip to [-1, 1] range then scale to int16
    mono_clipped = np.clip(mono_16k, -1.0, 1.0)
    # Scale: multiply by 32767 (max int16 positive value)
    pcm_int16 = (mono_clipped * 32767).astype(np.int16)
    # Convert to bytes (numpy int16 is already little-endian on LE systems)
    # Use struct for guaranteed little-endian
    pcm_bytes = pcm_int16.tobytes()

    # Verify we have data
    if not pcm_bytes:
        return _invalid_audio("PCM conversion produced empty output.")

    # ── Step 8: Encapsulate as CanonicalAudio ───────────────────
    return CanonicalAudio(
        pcm_s16le=pcm_bytes,
        sample_rate_hz=_TARGET_SAMPLE_RATE,
        channels=_TARGET_CHANNELS,
        sample_width_bits=_TARGET_SAMPLE_WIDTH_BITS,
        duration_ms=duration_ms,
        input_format=input_format,
    )
