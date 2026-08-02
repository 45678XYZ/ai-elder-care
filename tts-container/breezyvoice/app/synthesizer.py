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
from contextlib import contextmanager
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


@contextmanager
def _onnx_default_providers(onnxruntime, providers):
    """在這個區塊內，沒帶 `providers` 的 `InferenceSession` 會拿到預設值。

    為什麼需要：g2pw 0.1.1（PyPI 上的最後一版，2022 年）建 `InferenceSession` 時完全
    不帶 `providers`，而 ORT 從 1.9 起「只要 build 裡含 GPU provider 就必須明講
    providers」，否則直接拋 ValueError。CPU 版的 onnxruntime 沒有 GPU provider，所以
    一路相安無事；換成 onnxruntime-gpu 之後 g2pw 就必炸。上游 g2pW 的 master 已經補了
    `use_cuda` 參數，但那份修正從未發版，PyPI 上仍然只有 0.1.1。

    挑 CPU 是刻意的，不是將就：g2pw 那顆是做破音字消歧的小模型，CPU 綽綽有餘，而且這
    正是換 onnxruntime-gpu 之前的行為，等於沒有改變它。真正需要 GPU 的是 CosyVoice
    自己載入的 speech tokenizer 與聲紋比對，那兩處上游有明講 providers，不受這裡影響。

    只包住建構那一小段、事後還原，避免影響 CosyVoice 之後自己開的 session。
    """
    original = onnxruntime.InferenceSession

    def _session(*args, **kwargs):
        kwargs.setdefault("providers", providers)
        return original(*args, **kwargs)

    onnxruntime.InferenceSession = _session
    try:
        yield
    finally:
        onnxruntime.InferenceSession = original


class BreezyVoiceSynthesizer:
    """對 serving 層只暴露 `synthesize`；GPU 推論以 lock 串行化。"""

    def __init__(self, config: ContainerConfig) -> None:
        self._config = config
        # 單張 T4、單一 model instance，不能被多個 request 同時進入。
        self._lock = threading.Lock()
        self._prompt_cache: dict[Path, tuple[object, str]] = {}

        _ensure_import_path()
        try:
            import onnxruntime
            import torch
            from cosyvoice.utils.file_utils import load_wav
            from g2pw import G2PWConverter
            from single_inference import CustomCosyVoice, get_bopomofo_rare
        except Exception as exc:  # pragma: no cover - 僅在映像建置有誤時觸發
            raise SynthesisError("breezyvoice runtime is unavailable") from exc

        # 沒有 GPU 就讓容器起不來，不要退回 CPU。
        #
        # 這條路徑上的降級不會拋例外，也不會讓音質變差，只會慢一到兩個數量級——
        # /ping 照過、endpoint 照樣 InService，唯一的徵狀是長輩按下去之後一直沒有聲音，
        # 而那時已經付了好幾天的 GPU 機時。兩項都實際發生過，所以兩項都要擋：
        #
        # - torch：cu118 與 cu121 的 wheel 裝錯、或主機驅動太舊，`is_available()` 回 False。
        # - onnxruntime：CosyVoice 的 speech tokenizer 與聲紋比對走 onnx。CPU 版的
        #   onnxruntime 會被相依悄悄帶進來並覆蓋 GPU 版（見 Dockerfile 的說明），
        #   那兩段就整段掉回 CPU，而模型本身還在 GPU 上，從使用率看不出異常。
        if not torch.cuda.is_available():
            raise SynthesisError("cuda is unavailable; refusing to fall back to cpu")
        if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
            raise SynthesisError("onnxruntime has no cuda provider; refusing cpu")

        self._load_wav = load_wav
        self._get_bopomofo_rare = get_bopomofo_rare

        weights_dir = config.weights_dir
        if not weights_dir.is_dir():
            raise SynthesisError("model weights are missing")

        self._model = CustomCosyVoice(str(weights_dir))
        # 與上游 single_inference.main() 相同的設定：輸出拼音、容忍簡體輸入。
        # 外面那層是 g2pw 0.1.1 與 onnxruntime-gpu 的相容性修補，理由見
        # [_onnx_default_providers]。
        with _onnx_default_providers(onnxruntime, ["CPUExecutionProvider"]):
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
