"""
AWS zh-TW Adapter — capability gate、pre/postflight checks 與結果正規化。

僅接收 CanonicalAudio、固定 zh-TW、deadline、cancellation 與 correlation context。
任何未完整核准的 AWS capability gate 必回 route_not_approved 且 transport zero-call。
僅允許 ASR-only composition/test 注入 fake transport；不得 import 實際 AWS service、
SDK、network client、endpoint、Region、IAM 或儲存服務實作，且不得執行網路呼叫。

禁止依賴：handlers、HTTP、DB、AWS SDK、boto3、botocore、network client。
"""
from __future__ import annotations

from typing import Protocol

from .config import AwsCapabilityGate, make_route_not_approved_error
from .providers import AsrProvider, TransportRequest
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# FakeTransport Protocol — 僅供 test composition root 注入
# ─────────────────────────────────────────────────────────────────
class FakeTransport(Protocol):
    """
    Fake Transport 協定。

    僅供 ASR-only composition/test 注入。Production composition root 不提供實作。
    接受 TransportRequest，回傳文字結果或 raise exception 模擬 known/unknown errors。
    """

    def transcribe(self, request: TransportRequest) -> str | None: ...


# ─────────────────────────────────────────────────────────────────
# Known transport terminal exceptions
# ─────────────────────────────────────────────────────────────────
class TransportDeadlineExceeded(Exception):
    """Transport 層回報 deadline exceeded。"""
    pass


class TransportCancelled(Exception):
    """Transport 層回報已取消。"""
    pass


class TransportUnavailable(Exception):
    """Transport 層回報 provider unavailable。"""
    pass


# ─────────────────────────────────────────────────────────────────
# AwsZhAdapter — 符合 AsrProvider protocol
# ─────────────────────────────────────────────────────────────────
class AwsZhAdapter:
    """
    AWS zh-TW ASR Adapter。

    固定 precedence 決策：
    1. capability gate 不完整 → route_not_approved，zero transport call
    2. cancellation preflight → cancelled
    3. deadline preflight → deadline_exceeded
    4. invoke transport exactly once
    5. cancellation postflight → cancelled (overrides success)
    6. deadline postflight → deadline_exceeded (overrides success)
    7. normalize transport result
    """

    provider_id: str = "aws_zh"

    def __init__(
        self,
        capability_gate: AwsCapabilityGate,
        transport: FakeTransport | None = None,
    ) -> None:
        """
        初始化 AwsZhAdapter。

        Args:
            capability_gate: AWS capability gate（9 項全部核准才完整）。
            transport: 注入的 fake transport（僅供 test）。
                       Production 不提供 transport，gate 不完整時也不需要。
        """
        self._gate = capability_gate
        self._transport = transport

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> Transcript | TypedAsrError:
        """
        執行 zh-TW ASR transcription。

        固定 precedence：gate → cancel preflight → deadline preflight →
        transport (exactly once) → cancel postflight → deadline postflight →
        normalize result。
        """
        # ─── Step 1: Capability gate ───
        if not self._gate.is_complete:
            return make_route_not_approved_error(
                "AWS capability gate incomplete; all 9 items must be approved."
            )

        # ─── Step 2: Cancellation preflight ───
        if cancellation.is_triggered:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Cancelled before transport invocation.",
                retryable=False,
            )

        # ─── Step 3: Deadline preflight ───
        if deadline.is_expired():
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Deadline exceeded before transport invocation.",
                retryable=True,
            )

        # ─── Step 4: Invoke transport exactly once ───
        if self._transport is None:
            # No transport available — should not reach here in production
            # (gate should block), but fail safe.
            return make_route_not_approved_error(
                "No transport available for zh-TW route."
            )

        request = TransportRequest(
            audio=audio,
            language="zh-TW",
            deadline=deadline,
            cancellation=cancellation,
            correlation_id=context.correlation_id,
        )

        try:
            candidate = self._transport.transcribe(request)
        except TransportCancelled:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Transport reported cancellation.",
                retryable=False,
            )
        except TransportDeadlineExceeded:
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Transport reported deadline exceeded.",
                retryable=True,
            )
        except TransportUnavailable:
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
                message="Provider is currently unavailable.",
                retryable=True,
            )
        except Exception:
            # Unclassified exception — no raw exception text leakage
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_FAILURE,
                message="An unexpected provider error occurred.",
                retryable=True,
            )

        # ─── Step 5: Cancellation postflight ───
        if cancellation.is_triggered:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Cancelled after transport invocation.",
                retryable=False,
            )

        # ─── Step 6: Deadline postflight ───
        if deadline.is_expired():
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Deadline exceeded after transport invocation.",
                retryable=True,
            )

        # ─── Step 7: Normalize result ───
        return self._normalize(candidate)

    @staticmethod
    def _normalize(candidate: str | None) -> Transcript | TypedAsrError:
        """
        正規化 transport 結果。

        - 非 str 或 None → provider_invalid_response
        - 空白文字 → provider_invalid_response
        - 合格非空白文字 → Transcript（Unicode trimmed）
        """
        if not isinstance(candidate, str):
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_INVALID_RESPONSE,
                message="Transport returned non-text or empty result.",
                retryable=False,
            )

        trimmed = candidate.strip()
        if not trimmed:
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_INVALID_RESPONSE,
                message="Transport returned blank text after Unicode trim.",
                retryable=False,
            )

        return Transcript(text=trimmed)
