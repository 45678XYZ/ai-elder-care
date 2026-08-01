"""波形轉 MP3。

契約要求 response 一律是 `audio/mpeg` 的非空 MP3 bytes，而 BreezyVoice 產出的是
22050 Hz 浮點波形，因此這一層負責編碼。走 ffmpeg pipe 而不是先落地暫存檔，是為了
不讓合成內容以檔案形式留在容器磁碟上（見 docs/tts/security-and-pii.md）。
"""

from __future__ import annotations

import subprocess

import numpy as np

# 長者收聽情境用不到高位元率；32 kbps 單聲道已足夠，且能壓低回傳延遲與 S3 體積。
MP3_BITRATE = "32k"


class AudioEncodeError(RuntimeError):
    """編碼失敗；訊息不得包含合成文字或音訊內容。"""


def to_pcm16(samples: np.ndarray) -> bytes:
    """把 [-1, 1] 的浮點波形轉成 16-bit little-endian PCM。"""
    waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
    if waveform.size == 0:
        raise AudioEncodeError("empty waveform")
    # 先夾再轉，避免模型偶發溢位造成整數環繞而爆出雜訊。
    clipped = np.clip(waveform, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def encode_mp3(
    samples: np.ndarray,
    sample_rate: int,
    runner=subprocess.run,
) -> bytes:
    """將波形編成 MP3 bytes；`runner` 可注入以便測試不依賴 ffmpeg。"""
    pcm = to_pcm16(samples)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        MP3_BITRATE,
        "-f",
        "mp3",
        "pipe:1",
    ]
    try:
        completed = runner(command, input=pcm, capture_output=True, check=False)
    except OSError as exc:
        raise AudioEncodeError("ffmpeg unavailable") from exc

    if completed.returncode != 0:
        # 刻意不帶 ffmpeg stderr：它會複述輸入參數，屬於不必要的外洩面。
        raise AudioEncodeError("ffmpeg failed")
    if not completed.stdout:
        raise AudioEncodeError("empty mp3 output")
    return completed.stdout
