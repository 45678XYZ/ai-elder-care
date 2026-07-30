"""
本機推論的實體 ASR 模型 provider — Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3。

模型規格見 asr-lambda/docs/。這兩個 provider 在**本 process 內**執行推論，因此只
適用於有 GPU 的執行環境（GPU 容器／實例）。若模型改為託管在 SageMaker 端點上，
用的是 remote_endpoints.py 的遠端 provider，不是這裡。

推論 handle 不可重入、且 2B 參數模型重複載入會爆記憶體，所以併發一律由
ModelSlotPool 與 LazyModelHandle 把關（見 provider_base.py 的固定流程）。

重依賴（faster_whisper、transformers、torch、numpy）一律延遲 import：
放在模組頂層會讓 Lambda 冷啟載入數百 MB，也會讓不需要模型的單元測試無法執行。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from .concurrency import ModelSlotPool
from .config import validate_formo_prompt_id
from .provider_base import ModelProviderBase, guard
from .types import (
    CancellationSignal,
    CanonicalAudio,
    Deadline,
    Language,
)

# CE 的語言碼與領域 Language 的對應。CE 另支援 nan/en/id，但本專案的公開契約
# （docs/api.md）只有 zh-TW 與 hak，因此只映射這兩個。
_CE_LANGUAGE_CODES: dict[Language, str] = {
    Language.ZH_TW: "zh",
    Language.HAK: "hak",
}

# Formo 只做客語。收到其他語言代表設定接錯，屬於路由問題而非模型故障。
_FORMO_LANGUAGES: frozenset[Language] = frozenset({Language.HAK})

# Hugging Face token 只從執行環境讀取，且只在載入模型的瞬間使用。
# 不進設定物件、不進 telemetry、不寫檔。
_HF_TOKEN_ENV_KEYS = ("HUGGING_FACE_HUB_TOKEN", "HF_TOKEN")


@dataclass(frozen=True)
class LocalModelSpec:
    """實體模型的載入參數。"""

    model_id: str
    revision: str
    device: str = "cuda"
    compute_type: str = "float16"

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("LocalModelSpec.model_id must be non-blank.")
        if not self.revision.strip():
            raise ValueError("LocalModelSpec.revision must be non-blank.")


def _read_hf_token() -> str | None:
    """從環境讀取 HF token；不存在回 None。回傳值不得被記錄或序列化。"""
    for key in _HF_TOKEN_ENV_KEYS:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def pcm_s16le_to_float32(pcm_s16le: bytes) -> Any:
    """
    把 Canonical Audio 的 16-bit PCM 轉為模型要的 float32 波形（-1.0 ~ 1.0）。

    Canonical Audio 已保證 mono/16 kHz/S16LE，所以這裡不需要再 resample。
    """
    import numpy as np

    samples = np.frombuffer(pcm_s16le, dtype="<i2")
    return samples.astype(np.float32) / 32768.0


class _LocalModelProvider(ModelProviderBase):
    """本機模型 provider 的共用建構參數。"""

    def __init__(
        self,
        provider_id: str,
        spec: LocalModelSpec,
        slot_pool: ModelSlotPool,
        model_load_wait_seconds: float,
        load_retry_cooldown_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            slot_pool=slot_pool,
            handle_wait_seconds=model_load_wait_seconds,
            handle_name=f"{provider_id}:{spec.model_id}",
            load_retry_cooldown_seconds=load_retry_cooldown_seconds,
        )
        self._spec = spec


# ─────────────────────────────────────────────────────────────────
# Taiwan-Tongues-ASR-CE（faster-whisper / CTranslate2）
# ─────────────────────────────────────────────────────────────────
class CeLocalProvider(_LocalModelProvider):
    """
    Taiwan-Tongues-ASR-CE v2.0，以 faster-whisper 執行。

    可同時服務 zh-TW 與 hak，因此在備援鏈中既能當 zh-TW 的主力，也能當
    hak 的備援。

    注意：模型卡標明輸出文字不保證是指定語言（見 asr-lambda/docs/
    Taiwan-Tongues-ASR-CE.md），因此 transcript 的語言正確性必須由 Colab
    人工驗證階段判斷，本層只保證非空白。
    """

    def __init__(
        self,
        spec: LocalModelSpec,
        slot_pool: ModelSlotPool,
        model_load_wait_seconds: float,
        load_retry_cooldown_seconds: float = 60.0,
        provider_id: str = "ce_local",
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            spec=spec,
            slot_pool=slot_pool,
            model_load_wait_seconds=model_load_wait_seconds,
            load_retry_cooldown_seconds=load_retry_cooldown_seconds,
        )

    def _supports(self, language: Language) -> bool:
        return language in _CE_LANGUAGE_CODES

    def _build_handle(self) -> Any:
        from faster_whisper import WhisperModel

        # revision 不傳給 WhisperModel：不同 faster-whisper 版本對 revision 參數
        # 的支援不一致，這裡只用 metadata 記錄版本，避免綁定未驗證的參數。
        return WhisperModel(
            self._spec.model_id,
            device=self._spec.device,
            compute_type=self._spec.compute_type,
        )

    def _run_inference(
        self,
        handle: Any,
        audio: CanonicalAudio,
        language: Language,
        cancellation: CancellationSignal,
        deadline: Deadline,
    ) -> str | None:
        guard(cancellation, deadline)

        waveform = pcm_s16le_to_float32(audio.pcm_s16le)

        segments, _info = handle.transcribe(
            waveform,
            language=_CE_LANGUAGE_CODES[language],
            task="transcribe",
        )

        # faster-whisper 回傳 generator：逐段消費，讓取消與逾期有真正的
        # 生效點，而不是等整段音訊跑完才發現已經沒人要這個結果。
        parts: list[str] = []
        for segment in _as_iterable(segments):
            guard(cancellation, deadline)
            text = getattr(segment, "text", None)
            if isinstance(text, str):
                parts.append(text)

        return "".join(parts)


# ─────────────────────────────────────────────────────────────────
# FormoSpeech Whisper-v3（transformers）
# ─────────────────────────────────────────────────────────────────
class FormoLocalProvider(_LocalModelProvider):
    """
    FormoSpeech Whisper-v3，以 transformers 執行，只服務客語。

    腔調 Prompt ID 在建構時就通過精確 allowlist 驗證，之後只存在於推論的
    記憶體中；它是 telemetry、evidence 與 ADR 的禁止欄位。

    授權為 CC BY-NC 4.0（限非商業），且為 gated model——這兩件事由 config 的
    production gate 把關，不在本層判斷。
    """

    def __init__(
        self,
        spec: LocalModelSpec,
        slot_pool: ModelSlotPool,
        model_load_wait_seconds: float,
        prompt_id: str,
        load_retry_cooldown_seconds: float = 60.0,
        provider_id: str = "formo_local",
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            spec=spec,
            slot_pool=slot_pool,
            model_load_wait_seconds=model_load_wait_seconds,
            load_retry_cooldown_seconds=load_retry_cooldown_seconds,
        )
        # 建構期就驗證：不合法的 prompt ID 不該等到推論才發現。
        self._prompt_id = validate_formo_prompt_id(prompt_id)

    def _supports(self, language: Language) -> bool:
        return language in _FORMO_LANGUAGES

    def _build_handle(self) -> Any:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        token = _read_hf_token()
        processor = WhisperProcessor.from_pretrained(
            self._spec.model_id,
            revision=self._spec.revision,
            token=token,
        )
        model = WhisperForConditionalGeneration.from_pretrained(
            self._spec.model_id,
            revision=self._spec.revision,
            token=token,
            torch_dtype=(
                torch.float16 if self._spec.compute_type == "float16" else torch.float32
            ),
        )
        model.to(self._spec.device)
        model.eval()
        return _FormoHandle(processor=processor, model=model, device=self._spec.device)

    def _run_inference(
        self,
        handle: Any,
        audio: CanonicalAudio,
        language: Language,
        cancellation: CancellationSignal,
        deadline: Deadline,
    ) -> str | None:
        import torch

        guard(cancellation, deadline)

        waveform = pcm_s16le_to_float32(audio.pcm_s16le)

        features = handle.processor(
            waveform,
            sampling_rate=16_000,
            return_tensors="pt",
        ).input_features.to(handle.device)

        # TODO: 腔調 prompt 的確切傳遞方式需在 Colab 人工 gate 對照模型卡確認；
        # 這裡採 HF Whisper 標準的 prompt_ids 路徑，若模型卡另有規定須同步修正。
        prompt_ids = handle.processor.get_prompt_ids(
            self._prompt_id, return_tensors="pt"
        ).to(handle.device)

        guard(cancellation, deadline)

        with torch.no_grad():
            generated = handle.model.generate(
                features,
                prompt_ids=prompt_ids,
                language="zh",
                task="transcribe",
            )

        guard(cancellation, deadline)

        decoded = handle.processor.batch_decode(generated, skip_special_tokens=True)
        if not decoded:
            return None
        return decoded[0]


@dataclass
class _FormoHandle:
    """Formo 推論需要 processor 與 model 成對使用，包成單一 handle 一起載入。"""

    processor: Any
    model: Any
    device: str


def _as_iterable(value: Any) -> Iterable[Any]:
    """把 generator 或序列統一成可迭代物件；None 視為空。"""
    if value is None:
        return ()
    return value
