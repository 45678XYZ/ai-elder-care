"""
Tests for Safe Terminal Telemetry — telemetry.py。

驗證：
- allowlist 嚴格限制
- emit_once 語義
- deadline_outcome、terminal_outcome、elapsed_ms 投影
- 不包含禁止欄位
- SafeTelemetryRecord 建構驗證
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.asr.telemetry import (
    DeadlineOutcome,
    SafeTelemetryRecord,
    TELEMETRY_ALLOWLIST_KEYS,
    TerminalOutcome,
    TerminalTelemetryEmitter,
)
from src.shared.asr.types import (
    AsrErrorCategory,
    CanonicalAudio,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Test helper — TelemetrySink for testing
# ─────────────────────────────────────────────────────────────────
@dataclass
class CollectingSink:
    """收集所有 emit 的 SafeTelemetryRecord。"""

    records: list[SafeTelemetryRecord] = field(default_factory=list)

    def emit(self, record: SafeTelemetryRecord) -> None:
        self.records.append(record)


def _make_clock(values: list[float]):
    """建立一個依序回傳指定值的 clock。"""
    it = iter(values)
    return lambda: next(it)


def _make_canonical_audio() -> CanonicalAudio:
    """建立測試用 CanonicalAudio。"""
    # 16000 Hz, 1 channel, 16-bit, 100ms = 1600 samples = 3200 bytes
    pcm = b"\x00\x01" * 1600
    return CanonicalAudio(
        pcm_s16le=pcm,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=100,
        input_format=InputFormat.WAV,
    )


# ─────────────────────────────────────────────────────────────────
# SafeTelemetryRecord tests
# ─────────────────────────────────────────────────────────────────
class TestSafeTelemetryRecord:
    """SafeTelemetryRecord 驗證。"""

    def test_valid_success_record(self) -> None:
        record = SafeTelemetryRecord(
            correlation_id="abc-123",
            language="zh-TW",
            route="zh-TW",
            provider_id="aws_zh",
            input_format="wav",
            canonical_sample_rate_hz=16000,
            canonical_channels=1,
            audio_duration_ms=5000,
            deadline_outcome="not_reached",
            terminal_outcome="success",
            error_category=None,
            elapsed_ms=42,
            retryable=False,
        )
        assert record.terminal_outcome == "success"
        assert record.elapsed_ms == 42

    def test_valid_error_record(self) -> None:
        record = SafeTelemetryRecord(
            correlation_id="abc-123",
            language="hak",
            route="hak",
            provider_id="hak_mock",
            input_format="m4a",
            canonical_sample_rate_hz=None,
            canonical_channels=None,
            audio_duration_ms=None,
            deadline_outcome="deadline_exceeded",
            terminal_outcome="error",
            error_category="deadline_exceeded",
            elapsed_ms=0,
            retryable=True,
        )
        assert record.terminal_outcome == "error"
        assert record.deadline_outcome == "deadline_exceeded"

    def test_negative_elapsed_ms_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="elapsed_ms must be non-negative"):
            SafeTelemetryRecord(
                correlation_id="x",
                language="zh-TW",
                route="r",
                provider_id="p",
                input_format="wav",
                canonical_sample_rate_hz=None,
                canonical_channels=None,
                audio_duration_ms=None,
                deadline_outcome="not_reached",
                terminal_outcome="success",
                error_category=None,
                elapsed_ms=-1,
                retryable=False,
            )

    def test_invalid_terminal_outcome_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="terminal_outcome"):
            SafeTelemetryRecord(
                correlation_id="x",
                language="zh-TW",
                route="r",
                provider_id="p",
                input_format="wav",
                canonical_sample_rate_hz=None,
                canonical_channels=None,
                audio_duration_ms=None,
                deadline_outcome="not_reached",
                terminal_outcome="partial",
                error_category=None,
                elapsed_ms=0,
                retryable=False,
            )

    def test_invalid_deadline_outcome_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="deadline_outcome"):
            SafeTelemetryRecord(
                correlation_id="x",
                language="zh-TW",
                route="r",
                provider_id="p",
                input_format="wav",
                canonical_sample_rate_hz=None,
                canonical_channels=None,
                audio_duration_ms=None,
                deadline_outcome="maybe",
                terminal_outcome="success",
                error_category=None,
                elapsed_ms=0,
                retryable=False,
            )

    def test_to_dict_only_allowlist_keys(self) -> None:
        record = SafeTelemetryRecord(
            correlation_id="abc",
            language="zh-TW",
            route="zh-TW",
            provider_id="aws_zh",
            input_format="wav",
            canonical_sample_rate_hz=16000,
            canonical_channels=1,
            audio_duration_ms=5000,
            deadline_outcome="not_reached",
            terminal_outcome="success",
            error_category=None,
            elapsed_ms=10,
            retryable=False,
        )
        d = record.to_dict()
        assert set(d.keys()) <= TELEMETRY_ALLOWLIST_KEYS

    def test_validate_keys_discards_non_allowlist(self) -> None:
        data = {
            "correlation_id": "abc",
            "language": "zh-TW",
            "audio_bytes": b"secret",  # NOT allowed
            "transcript": "full text",  # NOT allowed
            "elapsed_ms": 10,
        }
        filtered = SafeTelemetryRecord.validate_keys(data)
        assert "audio_bytes" not in filtered
        assert "transcript" not in filtered
        assert "correlation_id" in filtered
        assert "elapsed_ms" in filtered


# ─────────────────────────────────────────────────────────────────
# TerminalTelemetryEmitter tests
# ─────────────────────────────────────────────────────────────────
class TestTerminalTelemetryEmitter:
    """TerminalTelemetryEmitter emit_once 語義。"""

    def test_emit_once_success(self) -> None:
        sink = CollectingSink()
        # start_time=0.0 is passed directly; clock is only called inside emit()
        clock = _make_clock([0.050])  # emit_time=50ms
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="corr-1"
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)
        emitter.set_route("zh-TW")
        emitter.set_provider_id("aws_zh")
        emitter.set_canonical_audio(_make_canonical_audio())

        transcript = Transcript(text="你好")
        emitter.emit(transcript)

        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.correlation_id == "corr-1"
        assert record.terminal_outcome == "success"
        assert record.deadline_outcome == "not_reached"
        assert record.error_category is None
        assert record.retryable is False
        assert record.elapsed_ms == 50
        assert record.canonical_sample_rate_hz == 16000
        assert record.canonical_channels == 1
        assert record.audio_duration_ms == 100

    def test_emit_once_error_deadline_exceeded(self) -> None:
        sink = CollectingSink()
        # start_time=1.0 passed directly; clock called inside emit() returns 1.123
        clock = _make_clock([1.123])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=1.0, correlation_id="corr-2"
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)

        error = TypedAsrError(
            category=AsrErrorCategory.DEADLINE_EXCEEDED,
            message="Deadline exceeded.",
            retryable=True,
        )
        emitter.emit(error)

        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.terminal_outcome == "error"
        assert record.deadline_outcome == "deadline_exceeded"
        assert record.error_category == "deadline_exceeded"
        assert record.retryable is True
        assert record.elapsed_ms == 123

    def test_emit_once_error_cancelled(self) -> None:
        sink = CollectingSink()
        clock = _make_clock([0.0, 0.010])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="corr-3"
        )

        error = TypedAsrError(
            category=AsrErrorCategory.CANCELLED,
            message="Cancelled.",
            retryable=False,
        )
        emitter.emit(error)

        assert len(sink.records) == 1
        assert sink.records[0].deadline_outcome == "cancelled"

    def test_emit_once_ignores_subsequent_calls(self) -> None:
        """emit_once 語義：首次 emit 後，後續呼叫被忽略。"""
        sink = CollectingSink()
        # 提供足夠的 clock values
        clock = _make_clock([0.0, 0.010, 0.020, 0.030])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="corr-4"
        )

        transcript = Transcript(text="first")
        error = TypedAsrError(
            category=AsrErrorCategory.INVALID_AUDIO,
            message="Error.",
            retryable=False,
        )

        emitter.emit(transcript)
        emitter.emit(error)
        emitter.emit(transcript)

        # 只有第一筆
        assert len(sink.records) == 1
        assert sink.records[0].terminal_outcome == "success"

    def test_has_emitted_property(self) -> None:
        sink = CollectingSink()
        clock = _make_clock([0.0, 0.001])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="x"
        )
        assert emitter.has_emitted is False
        emitter.emit(Transcript(text="hi"))
        assert emitter.has_emitted is True

    def test_canonical_audio_not_set_leaves_none(self) -> None:
        """canonicalize 失敗時 canonical fields 為 None。"""
        sink = CollectingSink()
        clock = _make_clock([0.0, 0.001])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="corr-5"
        )
        emitter.set_language(Language.HAK)
        emitter.set_input_format(InputFormat.M4A)

        error = TypedAsrError(
            category=AsrErrorCategory.INVALID_AUDIO,
            message="Bad audio.",
            retryable=False,
        )
        emitter.emit(error)

        record = sink.records[0]
        assert record.canonical_sample_rate_hz is None
        assert record.canonical_channels is None
        assert record.audio_duration_ms is None

    def test_elapsed_ms_non_negative_even_if_clock_goes_backward(self) -> None:
        """elapsed_ms 保證非負。"""
        sink = CollectingSink()
        # clock 回退（理論上不應發生但防禦性處理）
        clock = _make_clock([10.0, 9.0])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=10.0, correlation_id="corr-6"
        )
        emitter.emit(Transcript(text="test"))
        assert sink.records[0].elapsed_ms == 0

    def test_not_routed_sentinels(self) -> None:
        """未設定 route/provider 時使用 sentinel 值。"""
        sink = CollectingSink()
        clock = _make_clock([0.0, 0.001])
        emitter = TerminalTelemetryEmitter(
            sink=sink, clock=clock, start_time=0.0, correlation_id="corr-7"
        )
        emitter.emit(Transcript(text="x"))
        record = sink.records[0]
        assert record.route == "__not_routed__"
        assert record.provider_id == "__not_routed__"
