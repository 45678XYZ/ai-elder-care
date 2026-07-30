"""
ASR Provider Protocol 與 TransportRequest。

定義 AsrProvider protocol（transcribe 接受 CanonicalAudio）與
測試用 TransportRequest 型別。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .types import (
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)


class AsrProvider(Protocol):
    """
    ASR Provider 協定。

    transcribe 的第一個音訊參數永遠是 CanonicalAudio — 不接受原始 WAV/M4A bytes。
    """

    provider_id: str

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> Transcript | TypedAsrError: ...


@dataclass(frozen=True)
class AttemptRecord:
    """
    單一 provider 的一次嘗試結果。

    `admitted=False` 代表這個 provider 當下飽和、根本沒有執行推論。備援鏈需要
    區分「飽和」與「執行了但失敗」：前者是容量問題（應盡快溢流到下一個
    provider），後者是可用性問題（應記錄並轉移）。只看 TypedAsrError 分類無法
    分辨這兩者，所以獨立成欄位。
    """

    provider_id: str
    result: AsrTerminalResult
    admitted: bool
    queue_wait_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("AttemptRecord.provider_id must be a non-blank string.")
        if not isinstance(self.result, (Transcript, TypedAsrError)):
            raise TypeError(
                "AttemptRecord.result must be Transcript or TypedAsrError."
            )
        if self.queue_wait_ms < 0:
            raise ValueError("AttemptRecord.queue_wait_ms must be non-negative.")
        if self.admitted and isinstance(self.result, TypedAsrError):
            # 允許：被放行但推論失敗。這裡只擋不可能的組合（未放行卻成功）。
            return
        if not self.admitted and isinstance(self.result, Transcript):
            raise ValueError(
                "AttemptRecord cannot report a Transcript without admission."
            )


class ConcurrentAsrProvider(Protocol):
    """
    具併發閘門的 provider 擴充協定。

    實作者除了 AsrProvider.transcribe，另外提供 transcribe_with_admission，
    讓備援鏈取得取號等待時間與是否被放行。未實作此協定的 provider
    （如 mock 與 AWS adapter contract）由備援鏈以 transcribe 包裝，行為不變。
    """

    provider_id: str

    def transcribe_with_admission(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
        max_queue_wait_seconds: float,
    ) -> AttemptRecord: ...


@dataclass(frozen=True)
class TransportRequest:
    """
    測試用 Fake Transport 的請求型別。

    僅包含 CanonicalAudio、固定 language="zh-TW"、Deadline、CancellationSignal
    與 correlation_id。不得含 raw WAV/M4A bytes、HTTP payload、
    provider/endpoint/Region、HF Token、prompt ID。
    """

    audio: CanonicalAudio
    language: str  # 固定為 "zh-TW"
    deadline: Deadline
    cancellation: CancellationSignal
    correlation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.audio, CanonicalAudio):
            raise TypeError("TransportRequest.audio must be CanonicalAudio.")
        if self.language != "zh-TW":
            raise ValueError(
                f"TransportRequest.language must be 'zh-TW', got {self.language!r}."
            )
        if not isinstance(self.deadline, Deadline):
            raise TypeError("TransportRequest.deadline must be Deadline.")
        if not isinstance(self.cancellation, CancellationSignal):
            raise TypeError("TransportRequest.cancellation must be CancellationSignal.")
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError(
                "TransportRequest.correlation_id must be a non-blank string."
            )
