"""PCM 轉換；模型載入與實際推論需要 GPU，只能在 staging endpoint 驗證。"""

import numpy as np
import pytest

from app.transcriber import TranscriptionError, pcm_to_float32


def test_converts_pcm16_to_normalised_float():
    waveform = pcm_to_float32(np.array([0, 32767, -32768], dtype="<i2").tobytes())

    assert waveform.dtype == np.float32
    assert waveform[0] == pytest.approx(0.0)
    assert waveform[1] == pytest.approx(0.99997, abs=1e-4)
    assert waveform[2] == pytest.approx(-1.0)


def test_keeps_every_sample():
    assert len(pcm_to_float32(b"\x00\x01" * 1600)) == 1600


def test_stays_within_unit_range():
    """超出 [-1, 1] 會讓 feature extractor 算出錯誤的 log-mel。"""
    waveform = pcm_to_float32(np.arange(-32768, 32768, 257, dtype="<i2").tobytes())

    assert waveform.min() >= -1.0
    assert waveform.max() <= 1.0


def test_rejects_odd_length_body():
    with pytest.raises(TranscriptionError):
        pcm_to_float32(b"\x00\x01\x02")
