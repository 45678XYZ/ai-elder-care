"""
HakMockProvider — 客語 mock provider。

回傳決定性、非空白 Unicode 測試 Transcript。
輸出不依據音訊樣本、呼叫端文字、prompt 或完整逐字稿。
不建立模型、雲端或網路呼叫。

禁止依賴：handlers、HTTP、DB、AWS SDK、模型推論。
"""
from __future__ import annotations

from .types import (
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)

# 固定測試用回應 — 決定性、非空白 Unicode
_HAK_MOCK_TRANSCRIPT_TEXT = "客語測試轉錄結果"


class HakMockProvider:
    """
    客語 Mock Provider。

    接受 CanonicalAudio、hak Language、deadline、cancellation、context，
    回傳固定非空白 Unicode 測試 Transcript。

    輸出不根據 audio samples、source bytes、caller text、full transcript 或 prompt。
    不建立模型、網路或雲端呼叫。
    """

    provider_id: str = "hak_mock"

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> Transcript | TypedAsrError:
        """
        回傳固定的非空白 Unicode 測試 Transcript。

        不檢查 deadline/cancellation — router 負責 preflight 檢查。
        不使用 audio 內容產生輸出。
        """
        return Transcript(text=_HAK_MOCK_TRANSCRIPT_TEXT)
