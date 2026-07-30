"""
備援鏈測試 — 錯誤轉移、飽和溢流與不可轉移的終態。

最重要的一組斷言是「哪些錯誤不轉移」：`route_not_approved` 若能被備援繞過，
整個 fail-closed 設計就失效了。
"""
from __future__ import annotations

import time

import pytest

from src.shared.asr.failover import (
    DEFAULT_FAILOVER_CATEGORIES,
    FailoverChain,
    NON_FAILOVER_CATEGORIES,
)
from src.shared.asr.providers import AttemptRecord
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

CONTEXT = CorrelationContext(correlation_id="corr-chain-1")
AUDIO = CanonicalAudio(
    pcm_s16le=b"\x00\x01" * 1600,
    sample_rate_hz=16_000,
    channels=1,
    sample_width_bits=16,
    duration_ms=200,
    input_format=InputFormat.WAV,
)


def deadline_in(seconds: float) -> Deadline:
    return Deadline.after(seconds, time.monotonic)


def error(category: AsrErrorCategory, retryable: bool = True) -> TypedAsrError:
    return TypedAsrError(category=category, message="safe message", retryable=retryable)


class RecordingProvider:
    """實作 ConcurrentAsrProvider，記錄收到的取號預算與呼叫次數。"""

    def __init__(
        self,
        provider_id: str,
        result,
        admitted: bool = True,
        queue_wait_ms: int = 0,
        on_call=None,
    ) -> None:
        self.provider_id = provider_id
        self._result = result
        self._admitted = admitted
        self._queue_wait_ms = queue_wait_ms
        self._on_call = on_call
        self.calls = 0
        self.observed_budgets: list[float] = []

    def transcribe_with_admission(
        self, audio, language, deadline, cancellation, context, max_queue_wait_seconds
    ) -> AttemptRecord:
        self.calls += 1
        self.observed_budgets.append(max_queue_wait_seconds)
        if self._on_call is not None:
            self._on_call()
        return AttemptRecord(
            provider_id=self.provider_id,
            result=self._result,
            admitted=self._admitted,
            queue_wait_ms=self._queue_wait_ms,
        )


class LegacyProvider:
    """只實作 AsrProvider.transcribe，用來驗證備援鏈的向下相容包裝。"""

    def __init__(self, provider_id: str, result) -> None:
        self.provider_id = provider_id
        self._result = result
        self.calls = 0

    def transcribe(self, audio, language, deadline, cancellation, context):
        self.calls += 1
        return self._result


class ExplodingProvider:
    """直接拋例外而非回傳終態，模擬 provider 實作瑕疵。"""

    provider_id = "exploding"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, language, deadline, cancellation, context):
        self.calls += 1
        raise RuntimeError("token=super-secret internal trace")


def run(chain: FailoverChain, **overrides):
    kwargs = {
        "audio": AUDIO,
        "language": Language.HAK,
        "deadline": deadline_in(5.0),
        "cancellation": CancellationSignal(),
        "context": CONTEXT,
    }
    kwargs.update(overrides)
    return chain.run(
        kwargs["audio"],
        kwargs["language"],
        kwargs["deadline"],
        kwargs["cancellation"],
        kwargs["context"],
    )


# ─────────────────────────────────────────────────────────────────
# 分類集合本身的性質
# ─────────────────────────────────────────────────────────────────
def test_failover_and_non_failover_categories_partition_all_categories() -> None:
    """每個錯誤分類都必須明確歸類，不能有沒被決定的分類。"""
    assert DEFAULT_FAILOVER_CATEGORIES.isdisjoint(NON_FAILOVER_CATEGORIES)
    assert (
        DEFAULT_FAILOVER_CATEGORIES | NON_FAILOVER_CATEGORIES
    ) == set(AsrErrorCategory)


def test_route_not_approved_is_never_failover_eligible() -> None:
    """核准是管理決策，不得被備援繞過。"""
    assert AsrErrorCategory.ROUTE_NOT_APPROVED in NON_FAILOVER_CATEGORIES
    assert AsrErrorCategory.ROUTE_NOT_APPROVED not in DEFAULT_FAILOVER_CATEGORIES


# ─────────────────────────────────────────────────────────────────
# 基本行為
# ─────────────────────────────────────────────────────────────────
def test_primary_success_does_not_touch_backup() -> None:
    primary = RecordingProvider("primary", Transcript(text="主力結果"))
    backup = RecordingProvider("backup", Transcript(text="備援結果"))

    outcome = run(FailoverChain([primary, backup], spill_wait_seconds=0.1))

    assert isinstance(outcome.result, Transcript)
    assert outcome.result.text == "主力結果"
    assert outcome.attempt_count == 1
    assert outcome.failover_occurred is False
    assert outcome.served_provider_id == "primary"
    assert outcome.winning_provider_id == "primary"
    assert backup.calls == 0


def test_empty_chain_returns_route_not_approved_with_no_attempts() -> None:
    outcome = run(FailoverChain([], spill_wait_seconds=0.1))
    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert outcome.attempt_count == 0
    assert outcome.winning_provider_id is None


# ─────────────────────────────────────────────────────────────────
# 會觸發轉移的錯誤
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("category", sorted(DEFAULT_FAILOVER_CATEGORIES, key=str))
def test_failover_eligible_error_advances_to_backup(category) -> None:
    primary = RecordingProvider("primary", error(category))
    backup = RecordingProvider("backup", Transcript(text="備援結果"))

    outcome = run(FailoverChain([primary, backup], spill_wait_seconds=0.1))

    assert isinstance(outcome.result, Transcript)
    assert outcome.result.text == "備援結果"
    assert outcome.attempt_count == 2
    assert outcome.failover_occurred is True
    assert outcome.served_provider_id == "backup"
    assert backup.calls == 1


def test_saturated_primary_spills_to_backup() -> None:
    """容量問題也要溢流：主力排不到號時改用備援。"""
    primary = RecordingProvider(
        "primary",
        error(AsrErrorCategory.PROVIDER_UNAVAILABLE),
        admitted=False,
        queue_wait_ms=120,
    )
    backup = RecordingProvider(
        "backup", Transcript(text="備援結果"), queue_wait_ms=30
    )

    outcome = run(FailoverChain([primary, backup], spill_wait_seconds=0.2))

    assert isinstance(outcome.result, Transcript)
    assert outcome.attempts[0].admitted is False
    assert outcome.total_queue_wait_ms == 150


def test_all_providers_failing_returns_last_error() -> None:
    first = RecordingProvider("first", error(AsrErrorCategory.PROVIDER_UNAVAILABLE))
    second = RecordingProvider("second", error(AsrErrorCategory.PROVIDER_FAILURE))
    third = RecordingProvider(
        "third", error(AsrErrorCategory.PROVIDER_INVALID_RESPONSE, retryable=False)
    )

    outcome = run(FailoverChain([first, second, third], spill_wait_seconds=0.05))

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE
    assert outcome.attempt_count == 3
    assert outcome.served_provider_id == "third"
    assert outcome.winning_provider_id is None


# ─────────────────────────────────────────────────────────────────
# 不會觸發轉移的錯誤
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("category", sorted(NON_FAILOVER_CATEGORIES, key=str))
def test_non_failover_error_stops_chain_immediately(category) -> None:
    primary = RecordingProvider("primary", error(category))
    backup = RecordingProvider("backup", Transcript(text="不該被用到"))

    outcome = run(FailoverChain([primary, backup], spill_wait_seconds=0.1))

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is category
    assert outcome.attempt_count == 1
    assert outcome.failover_occurred is False
    assert backup.calls == 0


# ─────────────────────────────────────────────────────────────────
# 取消與逾期在每一棒之前重新檢查
# ─────────────────────────────────────────────────────────────────
def test_cancellation_between_attempts_stops_chain() -> None:
    cancellation = CancellationSignal()
    primary = RecordingProvider(
        "primary",
        error(AsrErrorCategory.PROVIDER_UNAVAILABLE),
        on_call=cancellation.trigger,
    )
    backup = RecordingProvider("backup", Transcript(text="不該被用到"))

    outcome = run(
        FailoverChain([primary, backup], spill_wait_seconds=0.1),
        cancellation=cancellation,
    )

    assert outcome.result.category is AsrErrorCategory.CANCELLED
    assert backup.calls == 0


def test_expired_deadline_before_first_attempt_stops_chain() -> None:
    primary = RecordingProvider("primary", Transcript(text="不該被用到"))
    outcome = run(
        FailoverChain([primary], spill_wait_seconds=0.1),
        deadline=deadline_in(-1.0),
    )
    assert outcome.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert primary.calls == 0
    assert outcome.attempt_count == 0


def test_deadline_expiring_between_attempts_stops_chain() -> None:
    primary = RecordingProvider(
        "primary",
        error(AsrErrorCategory.PROVIDER_UNAVAILABLE),
        on_call=lambda: time.sleep(0.12),
    )
    backup = RecordingProvider("backup", Transcript(text="不該被用到"))

    outcome = run(
        FailoverChain([primary, backup], spill_wait_seconds=0.01),
        deadline=deadline_in(0.05),
    )

    assert outcome.result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert backup.calls == 0


# ─────────────────────────────────────────────────────────────────
# 取號預算分配
# ─────────────────────────────────────────────────────────────────
def test_non_final_member_gets_spill_budget_and_final_gets_remaining_deadline() -> None:
    """非最後一棒要快速溢流；最後一棒後面沒人接，可用完剩餘 deadline。"""
    primary = RecordingProvider(
        "primary", error(AsrErrorCategory.PROVIDER_UNAVAILABLE)
    )
    backup = RecordingProvider("backup", Transcript(text="備援結果"))

    run(
        FailoverChain([primary, backup], spill_wait_seconds=0.2),
        deadline=deadline_in(5.0),
    )

    assert primary.observed_budgets[0] == pytest.approx(0.2, abs=0.01)
    assert backup.observed_budgets[0] > 1.0


# ─────────────────────────────────────────────────────────────────
# 相容性與防禦
# ─────────────────────────────────────────────────────────────────
def test_legacy_transcribe_only_provider_is_wrapped() -> None:
    legacy = LegacyProvider("legacy", Transcript(text="舊介面結果"))
    outcome = run(FailoverChain([legacy], spill_wait_seconds=0.1))

    assert isinstance(outcome.result, Transcript)
    assert outcome.attempts[0].admitted is True
    assert outcome.attempts[0].queue_wait_ms == 0
    assert legacy.calls == 1


def test_provider_raising_exception_becomes_provider_failure_and_chain_continues() -> None:
    exploding = ExplodingProvider()
    backup = RecordingProvider("backup", Transcript(text="備援結果"))

    outcome = run(FailoverChain([exploding, backup], spill_wait_seconds=0.05))

    assert isinstance(outcome.result, Transcript)
    assert outcome.attempts[0].result.category is AsrErrorCategory.PROVIDER_FAILURE
    assert "super-secret" not in outcome.attempts[0].result.message
    assert backup.calls == 1


def test_provider_returning_non_terminal_value_becomes_provider_failure() -> None:
    class JunkProvider:
        provider_id = "junk"

        def transcribe(self, *args, **kwargs):
            return {"text": "不是領域型別"}

    outcome = run(FailoverChain([JunkProvider()], spill_wait_seconds=0.05))
    assert outcome.result.category is AsrErrorCategory.PROVIDER_FAILURE


def test_provider_without_transcribe_becomes_provider_failure() -> None:
    class BrokenProvider:
        provider_id = "broken"

    outcome = run(FailoverChain([BrokenProvider()], spill_wait_seconds=0.05))
    assert outcome.result.category is AsrErrorCategory.PROVIDER_FAILURE


def test_chain_rejects_negative_spill_wait() -> None:
    with pytest.raises(ValueError):
        FailoverChain([], spill_wait_seconds=-1.0)


def test_provider_ids_exposes_configured_order() -> None:
    chain = FailoverChain(
        [
            RecordingProvider("a", Transcript(text="x")),
            RecordingProvider("b", Transcript(text="y")),
        ],
        spill_wait_seconds=0.1,
    )
    assert chain.provider_ids == ("a", "b")
