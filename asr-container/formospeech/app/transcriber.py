"""FormoSpeech Whisper-v3 推論包裝。

以 transformers 的 ASR pipeline 載入 artifact，並依上游模型卡固定
`language=<generation language>` 與 `prompt_ids=processor.get_prompt_ids(<腔調>)`。
prompt 在容器啟動時決定，request 不得攜帶（見 docs/asr/model-catalog.md 的部署邊界）。
"""

from __future__ import annotations

import threading

import numpy as np

from .config import BYTES_PER_SAMPLE, REQUIRED_SAMPLE_RATE_HZ, ContainerConfig

# PCM S16LE 的滿刻度；轉浮點時用它正規化到 [-1, 1]。
_INT16_FULL_SCALE = 32768.0


class TranscriptionError(RuntimeError):
    """推論失敗；訊息不得包含音訊、逐字稿或模型路徑。"""


def pcm_to_float32(body: bytes) -> np.ndarray:
    """把 canonical PCM S16LE bytes 轉成 pipeline 要的浮點波形。"""
    if len(body) % BYTES_PER_SAMPLE:
        raise TranscriptionError("malformed audio")
    samples = np.frombuffer(body, dtype="<i2").astype(np.float32)
    return samples / _INT16_FULL_SCALE


class FormoTranscriber:
    """對 serving 層只暴露 `transcribe`；GPU 推論以 lock 串行化。"""

    def __init__(self, config: ContainerConfig) -> None:
        self._config = config
        self._lock = threading.Lock()

        try:
            import torch
            from transformers import (
                AutoModelForSpeechSeq2Seq,
                AutoProcessor,
                pipeline,
            )
        except Exception as exc:  # pragma: no cover - 僅在映像建置有誤時觸發
            raise TranscriptionError("transformers runtime is unavailable") from exc

        weights_dir = config.weights_dir
        if not weights_dir.is_dir():
            raise TranscriptionError("model weights are missing")

        use_cuda = torch.cuda.is_available()
        device = "cuda:0" if use_cuda else "cpu"
        torch_dtype = torch.float16 if use_cuda else torch.float32

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(weights_dir),
            torch_dtype=torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        )
        model.to(device)
        processor = AutoProcessor.from_pretrained(str(weights_dir))

        self._pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            max_new_tokens=128,
            chunk_length_s=30,
            batch_size=16,
            torch_dtype=torch_dtype,
            device=device,
        )
        # prompt_ids 在啟動時算一次；每次 request 重算只是白費 CPU。
        self._generate_kwargs = {
            "language": config.generation_language,
            "prompt_ids": torch.from_numpy(
                processor.get_prompt_ids(config.prompt_id)
            ).to(device),
        }

    def transcribe(self, body: bytes) -> str:
        try:
            waveform = pcm_to_float32(body)
            with self._lock:
                result = self._pipeline(
                    {"raw": waveform, "sampling_rate": REQUIRED_SAMPLE_RATE_HZ},
                    generate_kwargs=self._generate_kwargs,
                )
        except TranscriptionError:
            raise
        except Exception as exc:
            # 原始例外可能帶著音訊統計或模型路徑，不往上傳。
            raise TranscriptionError("inference failed") from exc

        text = (result or {}).get("text", "")
        if not isinstance(text, str):
            raise TranscriptionError("invalid model output")

        # 上游會把 prompt 原樣接在輸出前面；契約只要辨識結果。
        prompt = self._config.prompt_id
        if text.startswith(prompt):
            text = text[len(prompt) :]

        text = text.strip()
        if not text:
            # 契約要求 text 為 trim 後非空白，空結果必須是錯誤而不是空字串。
            raise TranscriptionError("empty transcript")
        return text
