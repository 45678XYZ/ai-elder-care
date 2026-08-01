"""TTS 領域共用型別；不依賴 handler、資料庫或雲端 SDK。"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Callable


class Language(enum.Enum):
    """對外支援的語言。"""

    ZH_TW = "zh-TW"
    HAK = "hak"

    @classmethod
    def from_str(cls, value: str) -> "Language":
        return cls(value)


class HakkaDialect(enum.Enum):
    """教育部客語六腔代碼，與 elder profile 及固定端點名稱共用。"""

    SIXIAN = "htia_sixian"
    HAILU = "htia_hailu"
    DAPU = "htia_dapu"
    RAOPING = "htia_raoping"
    ZHAOAN = "htia_zhaoan"
    NANSIXIAN = "htia_nansixian"

    @classmethod
    def from_str(cls, value: str) -> "HakkaDialect":
        return cls(value)


class TtsErrorCategory(enum.Enum):
    """穩定且可安全記錄的 TTS 終態分類。"""

    INVALID_TEXT = "invalid_text"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    UNSUPPORTED_DIALECT = "unsupported_dialect"
    ROUTE_NOT_APPROVED = "route_not_approved"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_RESPONSE = "invalid_response"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TypedTtsError:
    """不夾帶輸入文字、模型輸出或憑證的領域錯誤。"""

    category: TtsErrorCategory
    message: str
    retryable: bool


@dataclass(frozen=True)
class SynthesizedAudio:
    """合成成功的 MP3 與實際服務 provider。"""

    data: bytes
    provider_id: str
    content_type: str = "audio/mpeg"
    extension: str = "mp3"

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("SynthesizedAudio.data must be non-empty.")
        if not self.provider_id.strip():
            raise ValueError("SynthesizedAudio.provider_id must be non-blank.")


TtsTerminalResult = SynthesizedAudio | TypedTtsError


@dataclass(frozen=True)
class CorrelationContext:
    """只保存可安全記錄的 request correlation ID。"""

    correlation_id: str

    def __post_init__(self) -> None:
        if not self.correlation_id or not self.correlation_id.strip():
            raise ValueError("correlation_id must be non-blank.")


@dataclass(frozen=True)
class Deadline:
    """以 monotonic clock 表示的絕對到期時間。"""

    expires_at: float
    clock: Callable[[], float]

    @classmethod
    def after(cls, seconds: float, clock: Callable[[], float]) -> "Deadline":
        return cls(expires_at=clock() + max(0.0, seconds), clock=clock)

    def is_expired(self) -> bool:
        return self.clock() >= self.expires_at


class CancellationSignal:
    """可由呼叫端觸發的協作式取消訊號。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        self._event.set()

    @property
    def is_triggered(self) -> bool:
        return self._event.is_set()
