"""BreezyVoice 推論包裝。

上游把可用的推論流程放在 repo 根目錄的 `single_inference.py`（而不是安裝成套件），
所以這裡把 repo 路徑掛上 `sys.path` 後直接沿用它的 `CustomCosyVoice` 與
`get_bopomofo_rare`，避免自己重抄一份文字正規化與注音轉換而與上游走鐘。

模型與 g2pW converter 在容器啟動時載入一次；BreezyVoice 是 zero-shot 模型，音色
來自打包在 artifact 內的固定聲紋，request 永遠不會帶聲音進來。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import numpy as np

from .config import PROMPT_SAMPLE_RATE, ContainerConfig

# 由 Dockerfile 決定 clone 位置；repo 根目錄要在 sys.path 上，`utils.word_utils`
# 這類 BreezyVoice 內部 import 才找得到。
BREEZYVOICE_REPO = Path(os.environ.get("BREEZYVOICE_REPO", "/opt/breezyvoice"))


class SynthesisError(RuntimeError):
    """推論失敗；訊息不得包含文字、聲音或模型路徑。"""


def _ensure_import_path() -> None:
    candidates = [BREEZYVOICE_REPO, BREEZYVOICE_REPO / "third_party" / "Matcha-TTS"]
    for path in candidates:
        entry = str(path)
        if path.is_dir() and entry not in sys.path:
            sys.path.insert(0, entry)


class BreezyVoiceSynthesizer:
    """對 serving 層只暴露 `synthesize`；GPU 推論以 lock 串行化。"""

    def __init__(self, config: ContainerConfig) -> None:
        self._config = config
        # 單張 T4、單一 model instance，不能被多個 request 同時進入。
        self._lock = threading.Lock()
        self._prompt_cache: dict[Path, tuple[object, str]] = {}

        _ensure_import_path()
        try:
            from cosyvoice.utils.file_utils import load_wav
            from g2pw import G2PWConverter
            from single_inference import CustomCosyVoice, get_bopomofo_rare
        except Exception as exc:  # pragma: no cover - 僅在映像建置有誤時觸發
            raise SynthesisError("breezyvoice runtime is unavailable") from exc

        self._load_wav = load_wav
        self._get_bopomofo_rare = get_bopomofo_rare

        weights_dir = config.weights_dir
        if not weights_dir.is_dir():
            raise SynthesisError("model weights are missing")

        self._model = CustomCosyVoice(str(weights_dir))
        # 與上游 single_inference.main() 相同的設定：輸出拼音、容忍簡體輸入。
        self._bopomofo_converter = G2PWConverter(
            style="pinyin", enable_non_tradional_chinese=True
        )

    def _load_prompt(self, speaker_dir: Path) -> tuple[object, str]:
        """載入聲紋音檔與逐字稿；同一個 speaker 只讀一次。"""
        cached = self._prompt_cache.get(speaker_dir)
        if cached is not None:
            return cached

        transcript_path = speaker_dir / "prompt.txt"
        if not transcript_path.is_file():
            raise SynthesisError("speaker prompt transcript is missing")
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        if not transcript:
            raise SynthesisError("speaker prompt transcript is empty")

        prompt_speech = self._load_wav(str(speaker_dir / "prompt.wav"), PROMPT_SAMPLE_RATE)
        # 逐字稿也要過注音轉換，否則罕用字的發音會與目標文字不一致。
        prompt = (prompt_speech, self._get_bopomofo_rare(transcript, self._bopomofo_converter))
        self._prompt_cache[speaker_dir] = prompt
        return prompt

    def synthesize(self, text: str, speaker_dir: Path) -> np.ndarray:
        try:
            # 聲紋快取一併納入 lock：serving 層以 threadpool 呼叫本方法，快取若在 lock 外
            # 會被多執行緒同時寫入。
            with self._lock:
                prompt_speech, prompt_text = self._load_prompt(speaker_dir)
                content = self._get_bopomofo_rare(text, self._bopomofo_converter)
                output = self._model.inference_zero_shot_no_normalize(
                    content, prompt_text, prompt_speech
                )
            speech = output["tts_speech"]
        except SynthesisError:
            raise
        except Exception as exc:
            # 原始例外可能帶著文字或模型路徑，不往上傳。
            raise SynthesisError("inference failed") from exc

        waveform = speech.detach().to("cpu").numpy() if hasattr(speech, "detach") else np.asarray(speech)
        return np.asarray(waveform, dtype=np.float32).reshape(-1)
