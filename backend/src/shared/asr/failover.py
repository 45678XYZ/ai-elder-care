"""
ASR 備援鏈 — 依序嘗試多個 provider，處理錯誤轉移與飽和溢流。

兩種觸發備援的情境，性質不同但處理方式一致（換下一個 provider）：

1. **可用性問題**：provider 執行了但失敗（不可用、推論爆炸、回傳無效內容）。
2. **容量問題**：provider 飽和，在允許的取號時間內排不到，根本沒執行。

不觸發備援的情境同樣重要：取消、逾期、音訊本身有問題、語言不支援、路由未核准。
這些換 provider 也不會有不同結果，甚至換了就等於繞過管制，因此一律立即終止。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config import (
    make_provider_failure_error,
    make_route_not_approved_error,
)
from .providers import AttemptRecord
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

# 允許轉移到下一個 provider 的錯誤分類。
#
# provider_invalid_response 的 retryable 是 False——那指的是「重試同一個
# provider 沒意義」。換一個實作不同的 provider 是另一回事，所以它列在這裡。
DEFAULT_FAILOVER_CATEGORIES: frozenset[AsrErrorCategory] = frozenset(
    {
        AsrErrorCategory.PROVIDER_UNAVAILABLE,
        AsrErrorCategory.PROVIDER_FAILURE,
        AsrErrorCategory.PROVIDER_INVALID_RESPONSE,
    }
)

# 明確不轉移的分類，列出來是為了讓「為什麼不轉移」可被讀到：
#
# - cancelled / deadline_exceeded：呼叫端已經不要這個結果了，再試是浪費資源。
# - invalid_audio / unsupported_audio_format / audio_duration_exceeded：
#   音訊本身的問題，任何 provider 都會得到同樣結論。
# - unsupported_language：語言不在契約內。
# - route_not_approved：這是管理決策，不是故障。用備援繞過未核准的路由，
#   等於讓 fail-closed 形同虛設；要開放就去改核准狀態，不要靠備援繞路。
_NO_PROVIDER_SENTINEL = "__not_routed__"

NON_FAILOVER_CATEGORIES: frozenset[AsrErrorCategory] = frozenset(
    {
        AsrErrorCategory.CANCELLED,
        AsrErrorCategory.DEADLINE_EXCEEDED,
        AsrErrorCategory.INVALID_AUDIO,
        AsrErrorCategory.UNSUPPORTED_AUDIO_FORMAT,
        AsrErrorCategory.AUDIO_DURATION_EXCEEDED,
        AsrErrorCategory.UNSUPPORTED_LANGUAGE,
        AsrErrorCategory.ROUTE_NOT_APPROVED,
    }
)


@dataclass(frozen=True)
class ChainOutcome:
    """
    備援鏈的整體結果。

    `served_provider_id` 是最終結果來自哪個 provider——成功時是勝出者，
    失敗時是最後一個嘗試者。telemetry 記的是這個值，因為它才反映
    「這次請求實際由誰處理」。
    """

    result: AsrTerminalResult
    attempts: tuple[AttemptRecord, ...]
    served_provider_id: str
    total_queue_wait_ms: int

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def failover_occurred(self) -> bool:
        """是否真的動用了備援（嘗試超過一個 provider）。"""
        return len(self.attempts) > 1

    @property
    def winning_provider_id(self) -> str | None:
        """產出 Transcript 的 provider；全部失敗時為 None。"""
        if isinstance(self.result, Transcript) and self.attempts:
            return self.attempts[-1].provider_id
        return None


class FailoverChain:
    """
    有序 provider 備援鏈。

    每次嘗試前都重新檢查取消與逾期，因此鏈越長也不會超出呼叫端的 deadline。
    非最後一棒的取號等待用 `spill_wait_seconds` 夾住（等不到就快速溢流），
    最後一棒才允許用完剩餘 deadline，因為它後面沒有備援可以接。
    """

    __slots__ = ("_providers", "_spill_wait_seconds", "_failover_categories")

    def __init__(
        self,
        providers: Sequence[object],
        spill_wait_seconds: float,
        failover_categories: frozenset[AsrErrorCategory] = DEFAULT_FAILOVER_CATEGORIES,
    ) -> None:
        if spill_wait_seconds < 0:
            raise ValueError("spill_wait_seconds must be non-negative.")
        self._providers = tuple(providers)
        self._spill_wait_seconds = spill_wait_seconds
        self._failover_categories = failover_categories

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(
            getattr(p, "provider_id", "__unknown__") for p in self._providers
        )

    def run(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> ChainOutcome:
        """依序嘗試 provider，回傳終態與完整嘗試紀錄。"""
        if not self._providers:
            return ChainOutcome(
                result=make_route_not_approved_error(
                    "No provider available for this route."
                ),
                attempts=(),
                served_provider_id=_NO_PROVIDER_SENTINEL,
                total_queue_wait_ms=0,
            )

        attempts: list[AttemptRecord] = []
        total_wait_ms = 0
        last_index = len(self._providers) - 1

        for index, provider in enumerate(self._providers):
            provider_id = getattr(provider, "provider_id", "__unknown__")

            # 每一棒之前重新檢查：備援不得在呼叫端已放棄後繼續消耗資源。
            if cancellation.is_triggered:
                return self._finalize(
                    attempts,
                    total_wait_ms,
                    fallback_provider_id=provider_id,
                    override=TypedAsrError(
                        category=AsrErrorCategory.CANCELLED,
                        message="Cancelled before provider attempt.",
                        retryable=False,
                    ),
                )
            if deadline.is_expired():
                return self._finalize(
                    attempts,
                    total_wait_ms,
                    fallback_provider_id=provider_id,
                    override=TypedAsrError(
                        category=AsrErrorCategory.DEADLINE_EXCEEDED,
                        message="Deadline exceeded before provider attempt.",
                        retryable=True,
                    ),
                )

            is_last = index == last_index
            max_queue_wait = (
                deadline.remaining_seconds()
                if is_last
                else min(self._spill_wait_seconds, deadline.remaining_seconds())
            )

            record = _attempt(
                provider,
                provider_id,
                audio,
                language,
                deadline,
                cancellation,
                context,
                max_queue_wait,
            )
            attempts.append(record)
            total_wait_ms += record.queue_wait_ms

            if isinstance(record.result, Transcript):
                break

            if not self._should_failover(record.result):
                break

            # 還有下一棒才繼續；最後一棒失敗就以它的錯誤作為終態。

        return self._finalize(attempts, total_wait_ms)

    def _should_failover(self, error: object) -> bool:
        return (
            isinstance(error, TypedAsrError)
            and error.category in self._failover_categories
        )

    @staticmethod
    def _finalize(
        attempts: list[AttemptRecord],
        total_wait_ms: int,
        fallback_provider_id: str = _NO_PROVIDER_SENTINEL,
        override: TypedAsrError | None = None,
    ) -> ChainOutcome:
        if override is not None:
            served = attempts[-1].provider_id if attempts else fallback_provider_id
            return ChainOutcome(
                result=override,
                attempts=tuple(attempts),
                served_provider_id=served,
                total_queue_wait_ms=total_wait_ms,
            )

        if not attempts:
            return ChainOutcome(
                result=make_route_not_approved_error(
                    "No provider attempt was made for this route."
                ),
                attempts=(),
                served_provider_id=fallback_provider_id,
                total_queue_wait_ms=total_wait_ms,
            )

        last = attempts[-1]
        return ChainOutcome(
            result=last.result,
            attempts=tuple(attempts),
            served_provider_id=last.provider_id,
            total_queue_wait_ms=total_wait_ms,
        )


def _attempt(
    provider: object,
    provider_id: str,
    audio: CanonicalAudio,
    language: Language,
    deadline: Deadline,
    cancellation: CancellationSignal,
    context: CorrelationContext,
    max_queue_wait_seconds: float,
) -> AttemptRecord:
    """
    呼叫單一 provider 並統一成 AttemptRecord。

    未實作 ConcurrentAsrProvider 的 provider（mock、AWS adapter contract）
    以 transcribe 包裝，視為必然放行、無取號等待。

    provider 若直接拋例外（而非回傳 TypedAsrError），在這裡收斂為
    provider_failure：一個 provider 的實作瑕疵不該讓整條鏈中斷。
    """
    admission = getattr(provider, "transcribe_with_admission", None)
    try:
        if callable(admission):
            record = admission(
                audio,
                language,
                deadline,
                cancellation,
                context,
                max_queue_wait_seconds,
            )
            if isinstance(record, AttemptRecord):
                return record
            # 協定被違反：當作 provider 故障，不猜測回傳值的意思。
            return AttemptRecord(
                provider_id=provider_id,
                result=make_provider_failure_error(
                    f"Provider {provider_id!r} returned an unexpected attempt record."
                ),
                admitted=False,
                queue_wait_ms=0,
            )

        transcribe = getattr(provider, "transcribe", None)
        if not callable(transcribe):
            return AttemptRecord(
                provider_id=provider_id,
                result=make_provider_failure_error(
                    f"Provider {provider_id!r} does not implement transcribe."
                ),
                admitted=False,
                queue_wait_ms=0,
            )

        result = transcribe(audio, language, deadline, cancellation, context)
        if not isinstance(result, (Transcript, TypedAsrError)):
            return AttemptRecord(
                provider_id=provider_id,
                result=make_provider_failure_error(
                    f"Provider {provider_id!r} returned a non-terminal result."
                ),
                admitted=True,
                queue_wait_ms=0,
            )
        return AttemptRecord(
            provider_id=provider_id,
            result=result,
            admitted=True,
            queue_wait_ms=0,
        )
    except Exception:
        # 不外洩原始例外文字。
        return AttemptRecord(
            provider_id=provider_id,
            result=make_provider_failure_error(
                f"Provider {provider_id!r} raised an unexpected error."
            ),
            admitted=False,
            queue_wait_ms=0,
        )
