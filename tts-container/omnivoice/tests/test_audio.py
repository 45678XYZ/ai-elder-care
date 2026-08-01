"""波形轉 MP3；契約要求回傳非空 MP3 bytes。"""

import subprocess

import numpy as np
import pytest

from app.audio import AudioEncodeError, encode_mp3, to_pcm16


def _completed(returncode=0, stdout=b"ID3mp3"):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_converts_float_waveform_to_pcm16():
    pcm = to_pcm16(np.array([0.0, 1.0, -1.0], dtype=np.float32))

    assert len(pcm) == 6
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [0, 32767, -32767]


def test_clips_out_of_range_samples_instead_of_wrapping():
    """模型偶發溢位若直接轉整數會環繞成反相尖峰，聽起來是爆音。"""
    pcm = np.frombuffer(to_pcm16(np.array([2.5, -2.5], dtype=np.float32)), dtype="<i2")

    assert pcm.tolist() == [32767, -32767]


def test_flattens_channel_dimension():
    pcm = to_pcm16(np.array([[0.0, 1.0]], dtype=np.float32))

    assert len(pcm) == 4


def test_rejects_empty_waveform():
    with pytest.raises(AudioEncodeError):
        to_pcm16(np.array([], dtype=np.float32))


def test_encodes_mono_mp3_at_source_sample_rate():
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return _completed()

    audio = encode_mp3(np.array([0.0, 0.5], dtype=np.float32), 22050, runner=runner)

    assert audio == b"ID3mp3"
    command = captured["command"]
    assert command[0] == "ffmpeg"
    assert "-ar" in command and command[command.index("-ar") + 1] == "22050"
    assert "-ac" in command and command[command.index("-ac") + 1] == "1"
    assert command[command.index("-f", command.index("-codec:a")) + 1] == "mp3"
    assert captured["input"] == to_pcm16(np.array([0.0, 0.5], dtype=np.float32))


def test_raises_when_ffmpeg_exits_non_zero():
    with pytest.raises(AudioEncodeError):
        encode_mp3(
            np.array([0.1], dtype=np.float32),
            22050,
            runner=lambda *a, **k: _completed(returncode=1, stdout=b""),
        )


def test_raises_when_ffmpeg_produces_no_audio():
    with pytest.raises(AudioEncodeError):
        encode_mp3(
            np.array([0.1], dtype=np.float32),
            22050,
            runner=lambda *a, **k: _completed(stdout=b""),
        )


def test_raises_when_ffmpeg_is_missing():
    def runner(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    with pytest.raises(AudioEncodeError):
        encode_mp3(np.array([0.1], dtype=np.float32), 22050, runner=runner)


def test_failure_message_excludes_ffmpeg_stderr():
    """ffmpeg stderr 會複述輸入參數，不該進到回應或 log。"""

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"Input #0, s16le, secret details"
        )

    with pytest.raises(AudioEncodeError) as excinfo:
        encode_mp3(np.array([0.1], dtype=np.float32), 22050, runner=runner)

    assert "secret" not in str(excinfo.value)
