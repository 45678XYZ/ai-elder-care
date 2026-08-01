"""
Tests for ASR Facade — facade.py。

驗證：
- 只接收 6 個輸入
- input gate（empty audio → invalid_audio）
- canonicalize 失敗時回傳 TypedAsrError
- 成功路由到 hak mock provider
- 每次 recognize 恰產生一筆 telemetry
- 不理解 HTTP、資料庫或對話工作流
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.asr.config import (
    AsrConfig,
       ProviderConfig,
    ProviderStatus,
    RouteConfig,
)
from src.shared.asr.facade import AsrFacade
from src.shared.asr.router import AsrRouter
from src.shared.asr.telemetry import SafeTelemetryRecord, TELEMETRY_ALLOWLIST_KEYS
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Test helper — TelemetrySink
# ─────────────────────────────────────────────────────────────────
@dataclass
class CollectingSink:
    """收集所有 emit 的 SafeTelemetryRecord。"""

    records: list[SafeTelemetryRecord] = field(default_factory=list)

    def emit(self, record: SafeTelemetryRecord) -> None:
        self.records.append(record)


# ─────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────
def _make_hak_config() -> AsrConfig:
    """建立啟用 hak mock 的設定。"""
    return AsrConfig(
        routes={
            "hak": RouteConfig(
                route="hak", provider_identifier="hak_mock", enabled=True
            ),
        },
        providers={
            "hak_mock": ProviderConfig(
                identifier="hak_mock", status=ProviderStatus.ENABLED
            ),
        },
        model_metadata={},
    )


def _make_facade(
    config: AsrConfig | None = None,
) -> tuple[AsrFacade, CollectingSink, list[float]]:
    """建立 facade、sink 與可控 clock values list。"""
    if config is None:
        config = _make_hak_config()
    router = AsrRouter(config)
    sink = CollectingSink()
    clock_values = [0.0, 0.050]  # start, emit
    idx = [0]

    def clock() -> float:
        val = clock_values[idx[0]] if idx[0] < len(clock_values) else clock_values[-1]
        idx[0] += 1
        return val

    facade = AsrFacade(router=router, telemetry_sink=sink, clock=clock)
    return facade, sink, clock_values


def _make_wav_bytes() -> bytes:
    """建立最小有效 WAV header + PCM data。"""
    import struct
    import io

    # 產生 minimal WAV: 16000 Hz, mono, 16-bit, 0.01s = 160 samples
    sample_rate = 16000
    num_samples = 160
    bits_per_sample = 16
    num_channels = 1
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align

    buf = io.BytesIO()
    # RIFF header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))  # PCM format
    buf.write(struct.pack("<H", num_channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", byte_rate))
    buf.write(struct.pack("<H", block_align))
    buf.write(struct.pack("<H", bits_per_sample))
    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    # samples: silence
    buf.write(b"\x00" * data_size)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────
class TestAsrFacadeInputGate:
    """Input gate 驗證。"""

    def test_empty_audio_bytes_returns_invalid_audio(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-1")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        result = facade.recognize(
            audio_bytes=b"",
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO
        assert result.retryable is False

    def test_empty_audio_bytes_emits_one_telemetry(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-2")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        facade.recognize(
            audio_bytes=b"",
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.terminal_outcome == "error"
        assert record.error_category == "invalid_audio"


class TestAsrFacadeCanonicalizeFailure:
    """Canonicalize 失敗。"""

    def test_corrupt_audio_returns_invalid_audio(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-3")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        # 提供不是有效 WAV 的 bytes
        result = facade.recognize(
            audio_bytes=b"this is not wav data at all",
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.INVALID_AUDIO

    def test_canonicalize_error_emits_telemetry(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-4")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        facade.recognize(
            audio_bytes=b"not_wav",
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.terminal_outcome == "error"
        # canonical fields should be None because canonicalize failed
        assert record.canonical_sample_rate_hz is None


class TestAsrFacadeHakRoute:
    """Hak mock provider route — 成功路徑。"""

    def test_valid_hak_returns_transcript(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-5")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        wav_bytes = _make_wav_bytes()
        result = facade.recognize(
            audio_bytes=wav_bytes,
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert isinstance(result, Transcript)
        assert result.text  # non-blank

    def test_valid_hak_emits_one_success_telemetry(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-6")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        wav_bytes = _make_wav_bytes()
        facade.recognize(
            audio_bytes=wav_bytes,
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.terminal_outcome == "success"
        assert record.correlation_id == "test-6"
        assert record.language == "hak"
        assert record.input_format == "wav"
        assert record.canonical_sample_rate_hz == 16000
        assert record.canonical_channels == 1
        assert record.deadline_outcome == "not_reached"


class TestAsrFacadeTelemetryAllowlist:
    """Safe Telemetry 只包含 allowlist 欄位。"""

    def test_telemetry_record_keys_within_allowlist(self) -> None:
        facade, sink, _ = _make_facade()
        context = CorrelationContext(correlation_id="test-7")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        wav_bytes = _make_wav_bytes()
        facade.recognize(
            audio_bytes=wav_bytes,
            input_format=InputFormat.WAV,
            language=Language.HAK,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert len(sink.records) == 1
        record = sink.records[0]
        record_dict = record.to_dict()
        assert set(record_dict.keys()) <= TELEMETRY_ALLOWLIST_KEYS


class TestAsrFacadeRouteNotApproved:
    """zh-TW route not approved（gate incomplete）。"""

    def test_zh_tw_gate_incomplete_returns_route_not_approved(self) -> None:
        # 使用包含 zh-TW route 但 gate 不完整的 config
        config = AsrConfig(
            routes={
                "zh-TW": RouteConfig(
                    route="zh-TW", provider_identifier="aws_zh", enabled=True
                ),
            },
            providers={
                "aws_zh": ProviderConfig(
                    identifier="aws_zh", status=ProviderStatus.ENABLED
                ),
            },
            model_metadata={},
        )
        facade, sink, _ = _make_facade(config)
        context = CorrelationContext(correlation_id="test-8")
        cancellation = CancellationSignal()
        deadline = Deadline.create(expiry=999.0, clock=lambda: 0.0)

        wav_bytes = _make_wav_bytes()
        result = facade.recognize(
            audio_bytes=wav_bytes,
            input_format=InputFormat.WAV,
            language=Language.ZH_TW,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED
        assert len(sink.records) == 1
