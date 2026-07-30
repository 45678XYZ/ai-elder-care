"""
Safe Terminal Telemetry — allowlist serializer 與 emit-once emitter。

每個 AsrFacade.recognize invocation 產生恰一筆 terminal telemetry。
emitter 持有 emit_once 狀態，重複 finalize 被忽略。

輸出鍵嚴格限於 allowlist；絕不保存 audio bytes、PCM samples、完整 transcript、
token、PII、raw response、raw exception 或 Formo Prompt ID。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, fields
from typing import Callable, Protocol

from .types import (
    AsrErrorCategory,
    CanonicalAudio,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Allowlist — 只有這些鍵可出現在 telemetry record
# ─────────────────────────────────────────────────────────────────
TELEMETRY_ALLOWLIST_KEYS: frozenset[str] = frozenset(
    {
        "correlation_id",
        "language",
        "route",
        "provider_id",
        "input_format",
        "canonical_sample_rate_hz",
        "canonical_channels",
        "audio_duration_ms",
        "deadline_outcome",
        "terminal_outcome",
        "error_category",
        "elapsed_ms",
        "retryable",
        # 併發與備援觀測欄位。全部是聚合數值或布林，不含任何內容資料——
        # 沒有它們就無法分辨「主力壞了但備援救回來」與「一次就成功」，
        # 也看不出流量是否已經在排隊。
        "attempt_count",
        "queue_wait_ms",
        "failover_occurred",
    }
)


# ─────────────────────────────────────────────────────────────────
# Outcome enums
# ─────────────────────────────────────────────────────────────────
class TerminalOutcome(enum.Enum):
    """Terminal outcome 值。"""

    SUCCESS = "success"
    ERROR = "error"


class DeadlineOutcome(enum.Enum):
    """Deadline outcome 值。"""

    NOT_REACHED = "not_reached"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


# ─────────────────────────────────────────────────────────────────
# Safe sentinels — route/provider 未路由時使用
# ─────────────────────────────────────────────────────────────────
_ROUTE_NOT_ROUTED: str = "__not_routed__"
_PROVIDER_NOT_ROUTED: str = "__not_routed__"


# ─────────────────────────────────────────────────────────────────
# SafeTelemetryRecord — 嚴格 allowlist 欄位
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SafeTelemetryRecord:
    """
    Safe Telemetry 紀錄 — 只含 allowlist 欄位。

    任何非 allowlist 鍵在建構時被拒絕。
    不保存 audio bytes、PCM samples、完整 transcript、token、PII、
    raw response、raw exception 或 Formo Prompt ID。
    """

    correlation_id: str
    language: str | None
    route: str
    provider_id: str
    input_format: str | None
    canonical_sample_rate_hz: int | None
    canonical_channels: int | None
    audio_duration_ms: int | None
    deadline_outcome: str
    terminal_outcome: str
    error_category: str | None
    elapsed_ms: int
    retryable: bool
    attempt_count: int = 0
    queue_wait_ms: int = 0
    failover_occurred: bool = False

    def __post_init__(self) -> None:
        # 驗證 elapsed_ms 非負
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative.")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative.")
        if self.queue_wait_ms < 0:
            raise ValueError("queue_wait_ms must be non-negative.")
        # 驗證 terminal_outcome 與 deadline_outcome 值
        valid_terminal = {v.value for v in TerminalOutcome}
        if self.terminal_outcome not in valid_terminal:
            raise ValueError(
                f"terminal_outcome must be one of {valid_terminal}, "
                f"got {self.terminal_outcome!r}."
            )
        valid_deadline = {v.value for v in DeadlineOutcome}
        if self.deadline_outcome not in valid_deadline:
            raise ValueError(
                f"deadline_outcome must be one of {valid_deadline}, "
                f"got {self.deadline_outcome!r}."
            )

    def to_dict(self) -> dict[str, str | int | bool | None]:
        """序列化為 dict，只含 allowlist 鍵。"""
        result: dict[str, str | int | bool | None] = {}
        for f in fields(self):
            if f.name in TELEMETRY_ALLOWLIST_KEYS:
                result[f.name] = getattr(self, f.name)
        return result

    @classmethod
    def validate_keys(cls, data: dict[str, object]) -> dict[str, object]:
        """
        驗證並過濾只留 allowlist 鍵。非 allowlist 鍵被丟棄。

        Returns:
            僅含 allowlist 鍵的 dict。
        """
        return {k: v for k, v in data.items() if k in TELEMETRY_ALLOWLIST_KEYS}


# ─────────────────────────────────────────────────────────────────
# TelemetrySink Protocol — injectable local interface
# ─────────────────────────────────────────────────────────────────
class TelemetrySink(Protocol):
    """
    Telemetry 接收端協定。

    由外部注入，接收 SafeTelemetryRecord。
    實作可能是 logger、metrics、queue 等。
    """

    def emit(self, record: SafeTelemetryRecord) -> None: ...


# ─────────────────────────────────────────────────────────────────
# TerminalTelemetryEmitter — emit_once 語義
# ─────────────────────────────────────────────────────────────────
class TerminalTelemetryEmitter:
    """
    每個 correlation context 恰一筆的 terminal telemetry emitter。

    emit_once 語義：首次 emit 後，後續呼叫被忽略。
    使用 injected monotonic clock 計算 elapsed_ms。
    """

    __slots__ = (
        "_sink",
        "_clock",
        "_start_time",
        "_emitted",
        "_lock",
        "_correlation_id",
        "_language",
        "_input_format",
        "_route",
        "_provider_id",
        "_canonical_audio",
        "_attempt_count",
        "_queue_wait_ms",
        "_failover_occurred",
    )

    def __init__(
        self,
        sink: TelemetrySink,
        clock: Callable[[], float],
        start_time: float,
        correlation_id: str,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._start_time = start_time
        self._emitted = False
        self._lock = threading.Lock()
        self._correlation_id = correlation_id
        self._language: str | None = None
        self._input_format: str | None = None
        self._route: str = _ROUTE_NOT_ROUTED
        self._provider_id: str = _PROVIDER_NOT_ROUTED
        self._canonical_audio: CanonicalAudio | None = None
        self._attempt_count: int = 0
        self._queue_wait_ms: int = 0
        self._failover_occurred: bool = False

    @property
    def has_emitted(self) -> bool:
        """是否已發送過 telemetry。"""
        return self._emitted

    def set_language(self, language: Language) -> None:
        """設定 language 欄位。"""
        self._language = language.value

    def set_input_format(self, input_format: InputFormat) -> None:
        """設定 input_format 欄位。"""
        self._input_format = input_format.value

    def set_route(self, route: str) -> None:
        """設定 route 欄位。"""
        self._route = route

    def set_provider_id(self, provider_id: str) -> None:
        """設定 provider_id 欄位。"""
        self._provider_id = provider_id

    def set_canonical_audio(self, audio: CanonicalAudio) -> None:
        """設定 canonical audio metadata（僅 sample_rate、channels、duration）。"""
        self._canonical_audio = audio

    def set_chain_metrics(
        self,
        attempt_count: int,
        queue_wait_ms: int,
        failover_occurred: bool,
    ) -> None:
        """
        設定備援鏈與併發的聚合觀測值。

        只接受數值與布林；provider 的原始回應、錯誤內容一律不進 telemetry。
        """
        self._attempt_count = max(0, int(attempt_count))
        self._queue_wait_ms = max(0, int(queue_wait_ms))
        self._failover_occurred = bool(failover_occurred)

    def emit(self, result: Transcript | TypedAsrError) -> None:
        """
        發送 terminal telemetry。

        emit_once 語義：首次呼叫發送紀錄，後續呼叫被忽略。
        """
        with self._lock:
            if self._emitted:
                return
            self._emitted = True

        # 計算 elapsed_ms（非負）
        elapsed_seconds = self._clock() - self._start_time
        elapsed_ms = max(0, int(elapsed_seconds * 1000))

        # 決定 terminal_outcome、deadline_outcome、error_category、retryable
        if isinstance(result, Transcript):
            terminal_outcome = TerminalOutcome.SUCCESS.value
            deadline_outcome = DeadlineOutcome.NOT_REACHED.value
            error_category = None
            retryable = False
        else:
            terminal_outcome = TerminalOutcome.ERROR.value
            error_category = result.category.value
            retryable = result.retryable

            # deadline_outcome 根據 error category 判斷
            if result.category == AsrErrorCategory.DEADLINE_EXCEEDED:
                deadline_outcome = DeadlineOutcome.DEADLINE_EXCEEDED.value
            elif result.category == AsrErrorCategory.CANCELLED:
                deadline_outcome = DeadlineOutcome.CANCELLED.value
            else:
                deadline_outcome = DeadlineOutcome.NOT_REACHED.value

        # Canonical audio metadata — 僅 canonicalize 成功才填
        canonical_sample_rate_hz: int | None = None
        canonical_channels: int | None = None
        audio_duration_ms: int | None = None

        if self._canonical_audio is not None:
            canonical_sample_rate_hz = self._canonical_audio.sample_rate_hz
            canonical_channels = self._canonical_audio.channels
            audio_duration_ms = self._canonical_audio.duration_ms

        record = SafeTelemetryRecord(
            correlation_id=self._correlation_id,
            language=self._language,
            route=self._route,
            provider_id=self._provider_id,
            input_format=self._input_format,
            canonical_sample_rate_hz=canonical_sample_rate_hz,
            canonical_channels=canonical_channels,
            audio_duration_ms=audio_duration_ms,
            deadline_outcome=deadline_outcome,
            terminal_outcome=terminal_outcome,
            error_category=error_category,
            elapsed_ms=elapsed_ms,
            retryable=retryable,
            attempt_count=self._attempt_count,
            queue_wait_ms=self._queue_wait_ms,
            failover_occurred=self._failover_occurred,
        )

        self._sink.emit(record)
