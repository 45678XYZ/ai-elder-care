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
