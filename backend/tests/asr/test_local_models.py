"""
實體模型 provider 骨架測試。

以 stub 取代真實推論，驗證 preflight／取號／postflight／錯誤正規化的固定流程。
不載入 faster-whisper 或 transformers、不下載模型、不連網。
"""
from __future__ import annotations

import threading
import time

import pytest

from src.shared.asr.concurrency import ModelSlotPool
from src.shared.asr.local_models import (
    _LocalModelProvider,
    LocalModelSpec,
    pcm_s16le_to_float32,
)
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

CONTEXT = CorrelationContext(correlation_id="corr-local-1")
SECRET_TEXT = "token=super-secret /home/user/audio.wav"


def make_audio(duration_ms: int = 200) -> CanonicalAudio:
    frames = int(16_000 * duration_ms / 1000)
    return CanonicalAudio(
        pcm_s16le=b"\x00\x01" * frames,
        sample_rate_hz=16_000,
        channels=1,
        sample_width_bits=16,
        duration_ms=duration_ms,
        input_format=InputFormat.WAV,
    )


def deadline_in(seconds: float) -> Deadline:
    return Deadline.after(seconds, time.monotonic)


class StubProvider(_LocalModelProvider):
    """以固定文字取代真實推論，可模擬延遲、例外與載入失敗。"""

    def __init__(
        self,
        pool: ModelSlotPool,
        text: str | None = "辨識結果",
        delay: float = 0.0,
        raise_in_inference: bool = False,
        raise_in_load: bool = False,
        languages: frozenset[Language] = frozenset(
            {Language.ZH_TW, Language.HAK}
        ),
    ) -> None:
        super().__init__(
            provider_id="stub",
            spec=LocalModelSpec(model_id="stub-model", revision="r1", device="cpu"),
            slot_pool=pool,
            model_load_wait_seconds=1.0,
            load_retry_cooldown_seconds=0.0,
        )
        self._text = text
        self._delay = delay
        self._raise_in_inference = raise_in_inference
        self._raise_in_load = raise_in_load
        self._languages = languages
        self.inference_calls = 0

    def _supports(self, language: Language) -> bool:
        return language in self._languages

    def _build_handle(self) -> object:
        if self._raise_in_load:
            raise RuntimeError(SECRET_TEXT)
        return object()

    def _run_inference(self, handle, audio, language, cancellation, deadline):
        self.inference_calls += 1
        assert isinstance(audio, CanonicalAudio), (
            "骨架必須把 CanonicalAudio 傳給 _run_inference，而非原始 bytes"
        )
        if self._raise_in_inference:
            raise RuntimeError(SECRET_TEXT)
        if self._delay:
            time.sleep(self._delay)
        return self._text


def attempt(provider: StubProvider, **overrides):
    kwargs = {
        "audio": make_audio(),
        "language": Language.HAK,
        "deadline": deadline_in(5.0),
        "cancellation": CancellationSignal(),
        "context": CONTEXT,
        "max_queue_wait_seconds": 1.0,
    }
    kwargs.update(overrides)
    return provider.transcribe_with_admission(
        kwargs["audio"],
        kwargs["language"],
        kwargs["deadline"],
        kwargs["cancellation"],
        kwargs["context"],
        kwargs["max_queue_wait_seconds"],
    )


# ─────────────────────────────────────────────────────────────────
# 波形轉換
# ─────────────────────────────────────────────────────────────────
def test_pcm_conversion_produces_normalised_float_waveform() -> None:
    waveform = pcm_s16le_to_float32(b"\x00\x00\xff\x7f\x00\x80")
    assert len(waveform) == 3
    assert waveform[0] == pytest.approx(0.0)
    assert waveform[1] == pytest.approx(1.0, abs=1e-4)
    assert waveform[2] == pytest.approx(-1.0, abs=1e-4)


# ─────────────────────────────────────────────────────────────────
# 成功路徑
# ─────────────────────────────────────────────────────────────────
def test_successful_attempt_returns_transcript_and_reports_admission() -> None:
    record = attempt(StubProvider(ModelSlotPool("stub", 1)))
    assert isinstance(record.result, Transcript)
    assert record.result.text == "辨識結果"
    assert record.admitted is True
    assert record.provider_id == "stub"
    assert record.queue_wait_ms >= 0


# ─────────────────────────────────────────────────────────────────
# 併發與飽和
# ─────────────────────────────────────────────────────────────────
def test_saturated_provider_reports_unavailable_without_running_inference() -> None:
    """飽和是容量問題：必須回 provider_unavailable 且 admitted=False，不執行推論。"""
    provider = StubProvider(ModelSlotPool("stub", 1), delay=0.3)
    holder = threading.Thread(target=lambda: attempt(provider))
    holder.start()
    time.sleep(0.08)

    denied = attempt(provider, max_queue_wait_seconds=0.0)
    holder.join()

    assert denied.admitted is False
    assert isinstance(denied.result, TypedAsrError)
    assert denied.result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    # 只有持有者那一次真的跑了推論
    assert provider.inference_calls == 1


def test_concurrent_requests_all_succeed_within_capacity() -> None:
    pool = ModelSlotPool("stub", 3)
    provider = StubProvider(pool, delay=0.02)
    outcomes = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        record = attempt(
            provider,
            context=CorrelationContext(correlation_id=f"corr-{index}"),
            max_queue_wait_seconds=5.0,
            deadline=deadline_in(10.0),
        )
        with lock:
            outcomes.append(record)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 9
    assert all(isinstance(o.result, Transcript) for o in outcomes)
    assert pool.stats().in_flight == 0


# ─────────────────────────────────────────────────────────────────
# 終態優先權
# ─────────────────────────────────────────────────────────────────
def test_triggered_cancellation_short_circuits_before_admission() -> None:
    provider = StubProvider(ModelSlotPool("stub", 1))
    cancelled = CancellationSignal()
    cancelled.trigger()

    record = attempt(provider, cancellation=cancelled)

    assert record.result.category is AsrErrorCategory.CANCELLED
    assert record.admitted is False
    assert provider.inference_calls == 0


def test_expired_deadline_short_circuits_before_admission() -> None:
    provider = StubProvider(ModelSlotPool("stub", 1))
    record = attempt(provider, deadline=deadline_in(-1.0))

    assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert record.admitted is False
    assert provider.inference_calls == 0


def test_cancellation_during_inference_wins_over_success() -> None:
    """推論期間被取消：成功結果不得覆蓋已取消終態。"""
    pool = ModelSlotPool("stub", 1)
    cancellation = CancellationSignal()

    class CancelMidway(StubProvider):
        def _run_inference(self, handle, audio, language, cancel, deadline):
            cancellation.trigger()
            return "本來會成功的文字"

    record = attempt(CancelMidway(pool), cancellation=cancellation)
    assert record.result.category is AsrErrorCategory.CANCELLED


def test_deadline_expiry_during_inference_wins_over_success() -> None:
    pool = ModelSlotPool("stub", 1)
    provider = StubProvider(pool, delay=0.15)
    record = attempt(provider, deadline=deadline_in(0.05))
    assert record.result.category is AsrErrorCategory.DEADLINE_EXCEEDED


# ─────────────────────────────────────────────────────────────────
# 錯誤正規化與不外洩
# ─────────────────────────────────────────────────────────────────
def test_inference_exception_becomes_provider_failure_without_leaking_text() -> None:
    record = attempt(
        StubProvider(ModelSlotPool("stub", 1), raise_in_inference=True)
    )
    assert record.result.category is AsrErrorCategory.PROVIDER_FAILURE
    assert record.admitted is True
    assert "super-secret" not in record.result.message
    assert "/home/user" not in record.result.message


def test_model_load_failure_becomes_provider_unavailable() -> None:
    """載入失敗是可轉移的可用性問題，不是 provider_failure。"""
    record = attempt(StubProvider(ModelSlotPool("stub", 1), raise_in_load=True))
    assert record.result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    assert "super-secret" not in record.result.message


@pytest.mark.parametrize("blank", [None, "", "   ", "\u3000\n\t"])
def test_blank_or_non_text_output_becomes_invalid_response(blank) -> None:
    record = attempt(StubProvider(ModelSlotPool("stub", 1), text=blank))
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_non_text_output_becomes_invalid_response() -> None:
    record = attempt(StubProvider(ModelSlotPool("stub", 1), text=12345))  # type: ignore[arg-type]
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_unsupported_language_is_route_not_approved() -> None:
    """語言接錯是路由設定問題，不是模型故障。"""
    provider = StubProvider(
        ModelSlotPool("stub", 1), languages=frozenset({Language.ZH_TW})
    )
    record = attempt(provider, language=Language.HAK)
    assert record.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert provider.inference_calls == 0


def test_provider_rejects_non_canonical_audio() -> None:
    """型別防線：provider 永遠不接受原始音訊 bytes。"""
    provider = StubProvider(ModelSlotPool("stub", 1))
    record = provider.transcribe_with_admission(
        b"RIFF....", Language.HAK, deadline_in(5.0), CancellationSignal(), CONTEXT, 1.0  # type: ignore[arg-type]
    )
    assert record.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE
    assert provider.inference_calls == 0


def test_transcribe_matches_asr_provider_protocol() -> None:
    """transcribe 必須回傳終態本身，供不需要 admission 資訊的呼叫端使用。"""
    provider = StubProvider(ModelSlotPool("stub", 1))
    result = provider.transcribe(
        make_audio(), Language.HAK, deadline_in(5.0), CancellationSignal(), CONTEXT
    )
    assert isinstance(result, Transcript)
