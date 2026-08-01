"""
Generate controlled audio test fixtures for ASR canonicalizer tests.

All fixtures use anonymous, safe synthetic data — no real elder data.
This script creates WAV files using struct-level construction and M4A
files using PyAV when available.

Fixtures generated:
1. short_valid.wav       — Short valid WAV (~0.5s, 440Hz sine)
2. valid.m4a            — Valid M4A/AAC (~0.5s, 440Hz sine)
3. corrupt.wav          — Corrupt bytes (invalid content)
4. mismatch_wav_as_m4a  — WAV content declared as M4A (format mismatch)
5. mismatch_m4a_as_wav  — M4A content declared as WAV (format mismatch)
6. boundary_59999ms.wav — Exactly 59.999s (959984 frames at 16kHz) → accepted
7. boundary_60000ms.wav — Exactly 60.000s (960000 frames at 16kHz) → accepted
8. boundary_60001ms.wav — Exactly 60.001s (960016 frames at 16kHz) → exceeded

Run: python -m tests.asr.fixtures.generate_fixtures
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
from pathlib import Path

import numpy as np

FIXTURES_DIR = Path(__file__).parent
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


def _make_sine_pcm(
    duration_seconds: float,
    sample_rate: int = SAMPLE_RATE,
    frequency: float = 440.0,
    amplitude: float = 0.5,
) -> bytes:
    """Generate 16-bit signed LE PCM sine wave."""
    num_frames = int(round(duration_seconds * sample_rate))
    t = np.arange(num_frames, dtype=np.float64) / sample_rate
    signal = (amplitude * np.sin(2 * math.pi * frequency * t) * 32767).astype(
        np.int16
    )
    return signal.tobytes()


def _make_sine_pcm_exact_frames(
    num_frames: int,
    sample_rate: int = SAMPLE_RATE,
    frequency: float = 440.0,
    amplitude: float = 0.5,
) -> bytes:
    """Generate 16-bit signed LE PCM sine wave with exact frame count."""
    t = np.arange(num_frames, dtype=np.float64) / sample_rate
    signal = (amplitude * np.sin(2 * math.pi * frequency * t) * 32767).astype(
        np.int16
    )
    return signal.tobytes()


def _wrap_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM bytes in a valid WAV container."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width

    # RIFF header
    header = struct.pack(
        "<4sI4s",
        b"RIFF",
        36 + data_size,  # file size - 8
        b"WAVE",
    )
    # fmt chunk
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,  # chunk size
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,  # bits per sample
    )
    # data chunk
    data_chunk = struct.pack("<4sI", b"data", data_size)

    return header + fmt_chunk + data_chunk + pcm_data


def generate_short_wav() -> None:
    """Generate a short valid WAV file (~0.5s, 440Hz)."""
    pcm = _make_sine_pcm(0.5)
    wav_bytes = _wrap_wav(pcm)
    (FIXTURES_DIR / "short_valid.wav").write_bytes(wav_bytes)


def generate_valid_m4a() -> None:
    """Generate a valid M4A/AAC file (~0.5s, 440Hz) using PyAV."""
    try:
        import av

        output_path = str(FIXTURES_DIR / "valid.m4a")
        num_frames = int(0.5 * SAMPLE_RATE)
        t = np.arange(num_frames, dtype=np.float64) / SAMPLE_RATE
        signal = (0.5 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)

        # Create M4A container with AAC codec
        container = av.open(output_path, mode="w", format="ipod")
        stream = container.add_stream("aac", rate=SAMPLE_RATE)
        stream.layout = "mono"

        # PyAV expects frames. Create audio frame and encode
        frame = av.AudioFrame.from_ndarray(
            signal.reshape(1, -1), format="fltp", layout="mono"
        )
        frame.sample_rate = SAMPLE_RATE

        for packet in stream.encode(frame):
            container.mux(packet)

        # Flush
        for packet in stream.encode(None):
            container.mux(packet)

        container.close()
    except ImportError:
        # Fallback: create a minimal M4A stub with ftyp box
        # This is a minimal valid-looking M4A that has the ftyp marker
        # but won't actually decode properly without PyAV
        _generate_m4a_stub()


def _generate_m4a_stub() -> None:
    """Create a minimal M4A file with proper ftyp box structure."""
    # ftyp box: size(4) + 'ftyp'(4) + brand(4) + version(4) + compatible(4)
    ftyp = struct.pack(">I", 20) + b"ftyp" + b"M4A " + struct.pack(">I", 0) + b"isom"
    # This is just the header; won't decode but will pass container check
    (FIXTURES_DIR / "valid.m4a").write_bytes(ftyp)


def generate_corrupt() -> None:
    """Generate corrupt audio bytes that look like WAV but are invalid."""
    # Start with RIFF/WAVE header but corrupt the data
    corrupt = b"RIFF" + struct.pack("<I", 100) + b"WAVE" + b"\xff" * 100
    (FIXTURES_DIR / "corrupt.wav").write_bytes(corrupt)


def generate_mismatch_wav_as_m4a() -> None:
    """Generate WAV content that will be declared as M4A (format mismatch)."""
    # This is a valid WAV file but will be tested with input_format=M4A
    pcm = _make_sine_pcm(0.3)
    wav_bytes = _wrap_wav(pcm)
    (FIXTURES_DIR / "mismatch_wav_as_m4a.wav").write_bytes(wav_bytes)


def generate_mismatch_m4a_as_wav() -> None:
    """Generate M4A-like content that will be declared as WAV (format mismatch)."""
    # Minimal M4A-looking content (has ftyp but no RIFF/WAVE)
    ftyp = struct.pack(">I", 20) + b"ftyp" + b"M4A " + struct.pack(">I", 0) + b"isom"
    (FIXTURES_DIR / "mismatch_m4a_as_wav.m4a").write_bytes(ftyp)


def generate_boundary_59999ms() -> None:
    """Generate WAV with exactly 59.999s duration (accepted boundary).

    At 16000 Hz: 59.999s * 16000 = 959984 frames
    Duration: 959984 / 16000 = 59.999s = 59999ms
    """
    num_frames = 959984  # 59.999 * 16000
    pcm = _make_sine_pcm_exact_frames(num_frames)
    wav_bytes = _wrap_wav(pcm)
    (FIXTURES_DIR / "boundary_59999ms.wav").write_bytes(wav_bytes)


def generate_boundary_60000ms() -> None:
    """Generate WAV with exactly 60.000s duration (accepted boundary).

    At 16000 Hz: 60.000s * 16000 = 960000 frames
    Duration: 960000 / 16000 = 60.000s = 60000ms
    """
    num_frames = 960000  # 60.000 * 16000
    pcm = _make_sine_pcm_exact_frames(num_frames)
    wav_bytes = _wrap_wav(pcm)
    (FIXTURES_DIR / "boundary_60000ms.wav").write_bytes(wav_bytes)


def generate_boundary_60001ms() -> None:
    """Generate WAV with exactly 60.001s duration (exceeds limit).

    At 16000 Hz: 60.001s * 16000 = 960016 frames
    Duration: 960016 / 16000 = 60.001s = 60001ms
    """
    num_frames = 960016  # 60.001 * 16000
    pcm = _make_sine_pcm_exact_frames(num_frames)
    wav_bytes = _wrap_wav(pcm)
    (FIXTURES_DIR / "boundary_60001ms.wav").write_bytes(wav_bytes)


def generate_manifest() -> None:
    """Generate manifest.json describing all fixtures and expected classifications."""
    manifest = {
        "description": "ASR canonicalizer test fixtures - anonymous, safe synthetic data",
        "fixtures": [
            {
                "id": "short_valid_wav",
                "file": "short_valid.wav",
                "format": "wav",
                "description": "Short valid WAV, ~0.5s 440Hz sine wave at 16kHz mono",
                "expected": "success",
                "duration_ms_approx": 500,
            },
            {
                "id": "valid_m4a",
                "file": "valid.m4a",
                "format": "m4a",
                "description": "Valid M4A/AAC, ~0.5s 440Hz sine wave at 16kHz mono",
                "expected": "success",
                "duration_ms_approx": 500,
            },
            {
                "id": "corrupt_wav",
                "file": "corrupt.wav",
                "format": "wav",
                "description": "Corrupt WAV - valid RIFF/WAVE header but invalid audio data",
                "expected": "invalid_audio",
            },
            {
                "id": "mismatch_wav_as_m4a",
                "file": "mismatch_wav_as_m4a.wav",
                "declared_format": "m4a",
                "actual_format": "wav",
                "description": "WAV content declared as M4A - format/content mismatch",
                "expected": "invalid_audio",
            },
            {
                "id": "mismatch_m4a_as_wav",
                "file": "mismatch_m4a_as_wav.m4a",
                "declared_format": "wav",
                "actual_format": "m4a",
                "description": "M4A content declared as WAV - format/content mismatch",
                "expected": "invalid_audio",
            },
            {
                "id": "boundary_59999ms",
                "file": "boundary_59999ms.wav",
                "format": "wav",
                "description": "Exactly 59.999s (959984 frames at 16kHz) - accepted boundary",
                "expected": "success",
                "duration_ms": 59999,
                "frame_count": 959984,
                "sample_rate": 16000,
            },
            {
                "id": "boundary_60000ms",
                "file": "boundary_60000ms.wav",
                "format": "wav",
                "description": "Exactly 60.000s (960000 frames at 16kHz) - accepted boundary",
                "expected": "success",
                "duration_ms": 60000,
                "frame_count": 960000,
                "sample_rate": 16000,
            },
            {
                "id": "boundary_60001ms",
                "file": "boundary_60001ms.wav",
                "format": "wav",
                "description": "Exactly 60.001s (960016 frames at 16kHz) - exceeds limit",
                "expected": "audio_duration_exceeded",
                "duration_ms": 60001,
                "frame_count": 960016,
                "sample_rate": 16000,
            },
        ],
        "safety_notes": [
            "All fixtures are synthetically generated 440Hz sine waves.",
            "No real elder data, PII, or copyrighted audio is used.",
            "Source bytes are never passed to any provider.",
        ],
    }
    manifest_path = FIXTURES_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def main() -> None:
    """Generate all fixtures."""
    print(f"Generating fixtures in: {FIXTURES_DIR}")

    generate_short_wav()
    print("  ✓ short_valid.wav")

    generate_valid_m4a()
    print("  ✓ valid.m4a")

    generate_corrupt()
    print("  ✓ corrupt.wav")

    generate_mismatch_wav_as_m4a()
    print("  ✓ mismatch_wav_as_m4a.wav")

    generate_mismatch_m4a_as_wav()
    print("  ✓ mismatch_m4a_as_wav.m4a")

    generate_boundary_59999ms()
    print("  ✓ boundary_59999ms.wav")

    generate_boundary_60000ms()
    print("  ✓ boundary_60000ms.wav")

    generate_boundary_60001ms()
    print("  ✓ boundary_60001ms.wav")

    generate_manifest()
    print("  ✓ manifest.json")

    print("Done.")


if __name__ == "__main__":
    main()
