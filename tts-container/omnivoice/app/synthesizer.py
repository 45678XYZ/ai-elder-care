"""OmniVoice 推論包裝。

上游把客語版本發在 FormoSpeech/OmniVoice-hakka，安裝後以 `omnivoice.OmniVoice`
載入。模型與聲紋在容器啟動時載入一次；腔調透過 `instruct` 參數指定，對照表在
config.py，request 只能送 wire value，不能送任意 instruct 字串。
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .config import DIALECT_INSTRUCTIONS, ContainerConfig


class SynthesisError(RuntimeError):
    """推論失敗；訊息不得包含文字、聲音或模型路徑。"""


class OmniVoiceSynthesizer:
    """對 serving 層只暴露 `synthesize`；GPU 推論以 lock 串行化。"""

    def __init__(self, config: ContainerConfig) -> None:
        self._config = config
        # 單張 GPU、單一 model instance，不能被多個 request 同時進入。
        self._lock = threading.Lock()
        self._prompt_cache: dict[Path, tuple[str, str]] = {}

        try:
            import torch
            from omnivoice import OmniVoice
        except Exception as exc:  # pragma: no cover - 僅在映像建置有誤時觸發
            raise SynthesisError("omnivoice runtime is unavailable") from exc

        weights_dir = config.weights_dir
        if not weights_dir.is_dir():
            raise SynthesisError("model weights are missing")

        # 從已解壓的 artifact 載入，不在執行期回頭連 Hugging Face（endpoint 可能無外網，
        # 且 gated repo 的 token 依規定不得進入 SageMaker environment）。
        self._model = OmniVoice.from_pretrained(
            str(weights_dir),
            device_map="cuda:0" if torch.cuda.is_available() else "cpu",
            dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        )

    def _load_prompt(self, speaker_dir: Path) -> tuple[str, str]:
        """回傳聲紋音檔路徑與逐字稿；同一個 speaker 只讀一次。"""
        cached = self._prompt_cache.get(speaker_dir)
        if cached is not None:
            return cached

        transcript_path = speaker_dir / "prompt.txt"
        if not transcript_path.is_file():
            raise SynthesisError("speaker prompt transcript is missing")
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        if not transcript:
            raise SynthesisError("speaker prompt transcript is empty")

        prompt = (str(speaker_dir / "prompt.wav"), transcript)
        self._prompt_cache[speaker_dir] = prompt
        return prompt

    def synthesize(self, text: str, dialect: str, speaker_dir: Path) -> np.ndarray:
        instruct = DIALECT_INSTRUCTIONS.get(dialect)
        if instruct is None:
            # 正常情況下 contract 已擋掉，這裡是部署設定與對照表不同步時的保險。
            raise SynthesisError("dialect is not serviceable")

        try:
            # 聲紋快取一併納入 lock：serving 層以 threadpool 呼叫本方法，快取若在 lock
            # 外會被多執行緒同時寫入。
            with self._lock:
                ref_audio, ref_text = self._load_prompt(speaker_dir)
                audio = self._model.generate(
                    text=text,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    instruct=instruct,
                )
        except SynthesisError:
            raise
        except Exception as exc:
            # 原始例外可能帶著文字或模型路徑，不往上傳。
            raise SynthesisError("inference failed") from exc

        # 上游範例是 sf.write(path, audio[0], 24000)，第一維是 batch。
        waveform = audio[0] if len(audio) else audio
        if hasattr(waveform, "detach"):
            waveform = waveform.detach().to("cpu").numpy()
        return np.asarray(waveform, dtype=np.float32).reshape(-1)
