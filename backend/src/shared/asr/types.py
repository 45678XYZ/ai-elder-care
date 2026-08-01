"""
ASR 領域不可變型別與驗證。

本模組定義 InputFormat、Language、CorrelationContext、CanonicalAudio、
Deadline、CancellationSignal、Transcript、TypedAsrError 與互斥 terminal result。

禁止依賴：handlers、HTTP、DB、AWS SDK、codec、telemetry sink。
"""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Callable, Union


# ─────────────────────────────────────────────────────────────────
# InputFormat — 僅 wav、m4a
# ─────────────────────────────────────────────────────────────────
class InputFormat(enum.Enum):
    """ASR 支援的來源音訊格式。"""

    WAV = "wav"
    M4A = "m4a"

    @classmethod
    def from_str(cls, value: str) -> "InputFormat":
        """從字串解析 InputFormat，不支援的格式 raise ValueError。"""
        try:
            return cls(value)
        except ValueError:
            raise ValueError(
                f"Unsupported audio format: {value!r}. "
                f"Only 'wav' and 'm4a' are supported."
            )


# ─────────────────────────────────────────────────────────────────
# Language — 僅 zh-TW、hak
# ─────────────────────────────────────────────────────────────────
class Language(enum.Enum):
    """ASR 支援的辨識語言。"""

    ZH_TW = "zh-TW"
    HAK = "hak"

    @classmethod
    def from_str(cls, value: str) -> "Language":
        """從字串解析 Language，未知值 raise ValueError。"""
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(
            f"Unsupported language: {value!r}. "
            f"Only 'zh-TW' and 'hak' are supported."
        )


class HakkaDialect(enum.Enum):
    """用來選擇已固定 prompt 的客語端點；不會傳入推論 payload。"""

    SIXIAN = "htia_sixian"
    HAILU = "htia_hailu"
    DAPU = "htia_dapu"
    RAOPING = "htia_raoping"
    ZHAOAN = "htia_zhaoan"
    NANSIXIAN = "htia_nansixian"

    @classmethod
    def from_str(cls, value: str) -> "HakkaDialect":
        return cls(value)


# ─────────────────────────────────────────────────────────────────
# AsrErrorCategory — 完整列舉
# ─────────────────────────────────────────────────────────────────
class AsrErrorCategory(enum.Enum):
    """Typed ASR Error 的固定錯誤分類。"""

    INVALID_AUDIO = "invalid_audio"
    UNSUPPORTED_AUDIO_FORMAT = "unsupported_audio_format"
    AUDIO_DURATION_EXCEEDED = "audio_duration_exceeded"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    ROUTE_NOT_APPROVED = "route_not_approved"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    PROVIDER_FAILURE = "provider_failure"


# ─────────────────────────────────────────────────────────────────
# TypedAsrError — 不可變、不承載 raw exception/audio/token
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TypedAsrError:
    """
    ASR 領域具型別錯誤結果。

    包含固定 category、安全 message 與 retryable 旗標。
    不承載 raw exception、audio bytes、token 或 provider raw response。
    """

    category: AsrErrorCategory
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.category, AsrErrorCategory):
            raise TypeError(
                f"category must be AsrErrorCategory, got {type(self.category).__name__}"
            )


# ─────────────────────────────────────────────────────────────────
# Transcript — 經 Unicode trim 後非空白文字
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Transcript:
    """
    ASR 成功結果：經 Unicode trim 後非空白的文字。

    只存在於成功結果中；不得寫入 Safe Telemetry/evidence/ADR。
    """

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Transcript text must be non-blank after Unicode trim.")
        # 儲存 trimmed 版本
        object.__setattr__(self, "text", self.text.strip())


# ─────────────────────────────────────────────────────────────────
# AsrTerminalResult — 互斥 union
# ─────────────────────────────────────────────────────────────────
AsrTerminalResult = Union[Transcript, TypedAsrError]
"""互斥 union：Transcript 或 TypedAsrError，不存在第三種或 partial 狀態。"""


# ─────────────────────────────────────────────────────────────────
# CorrelationContext — 僅持有非空白 correlation_id
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CorrelationContext:
    """
    呼叫關聯資訊。

    僅持有非空白、不透明 correlation_id。
    不可帶 audio、transcript、token、長者資料、prompt ID 或任意呼叫端 metadata。
    """

    correlation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError(
                "CorrelationContext.correlation_id must be a non-blank string."
            )
        # 正規化為 stripped 值
        object.__setattr__(self, "correlation_id", self.correlation_id.strip())


# ─────────────────────────────────────────────────────────────────
# CanonicalAudio — 不可變、不得序列化到 telemetry/evidence
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CanonicalAudio:
    """
    單聲道、16,000 Hz、16-bit signed little-endian PCM 音訊。

    包含精確 duration_ms 與僅供診斷的 input_format。
    不得序列化到 telemetry/evidence。
    """

    pcm_s16le: bytes
    sample_rate_hz: int
    channels: int
    sample_width_bits: int
    duration_ms: int
    input_format: InputFormat

    def __post_init__(self) -> None:
        if not isinstance(self.pcm_s16le, bytes) or len(self.pcm_s16le) == 0:
            raise ValueError("CanonicalAudio.pcm_s16le must be non-empty bytes.")
        if self.sample_rate_hz != 16000:
            raise ValueError("CanonicalAudio.sample_rate_hz must be 16000.")
        if self.channels != 1:
            raise ValueError("CanonicalAudio.channels must be 1.")
        if self.sample_width_bits != 16:
            raise ValueError("CanonicalAudio.sample_width_bits must be 16.")
        if not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ValueError(
                "CanonicalAudio.duration_ms must be a non-negative integer."
            )
        if not isinstance(self.input_format, InputFormat):
            raise TypeError("CanonicalAudio.input_format must be InputFormat.")


# ─────────────────────────────────────────────────────────────────
# Deadline — 單調時鐘絕對到期時刻
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Deadline:
    """
    呼叫端提供的單調時鐘絕對到期時刻。

    使用 injected monotonic clock 判斷是否到期，不使用 wall clock。
    """

    expiry: float
    _clock: Callable[[], float]

    def is_expired(self) -> bool:
        """檢查 deadline 是否已到期。"""
        return self._clock() >= self.expiry

    def remaining_seconds(self) -> float:
        """回傳剩餘秒數；已到期時固定為零。"""
        return max(0.0, self.expiry - self._clock())

    @classmethod
    def create(
        cls, expiry: float, clock: Callable[[], float]
    ) -> "Deadline":
        """以 injected monotonic clock 建立 Deadline。"""
        return cls(expiry=expiry, _clock=clock)

    @classmethod
    def after(
        cls, seconds: float, clock: Callable[[], float]
    ) -> "Deadline":
        """以「從現在起 N 秒」建立 Deadline，供呼叫端換算 Lambda 剩餘時間使用。"""
        return cls(expiry=clock() + seconds, _clock=clock)


# ─────────────────────────────────────────────────────────────────
# CancellationSignal — 一旦觸發不可回復
# ─────────────────────────────────────────────────────────────────
class CancellationSignal:
    """
    可查詢的協作式取消狀態。

    一旦觸發不可回復；後續查詢永遠回傳 True。
    """

    __slots__ = ("_triggered", "_lock")

    def __init__(self) -> None:
        self._triggered = False
        self._lock = threading.Lock()

    @property
    def is_triggered(self) -> bool:
        """查詢是否已被取消。"""
        return self._triggered

    def trigger(self) -> None:
        """觸發取消。一旦觸發不可回復。"""
        with self._lock:
            self._triggered = True

    def __repr__(self) -> str:
        return f"CancellationSignal(triggered={self._triggered})"
