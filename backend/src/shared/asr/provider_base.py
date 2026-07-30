"""
模型型 provider 的共用骨架。

遠端端點推論（remote_endpoints.py）的固定安全流程：

    cancel/deadline preflight → 取號（bounded） → 取 handle（bounded）
      → 推論 → cancel/deadline postflight → 正規化輸出

把它集中在一處，才不會有某個 provider 漏掉 postflight 檢查、或不小心把原始例外
文字外洩。子類別只需實作 `_build_handle`、`_run_inference` 與 `_supports`。

禁止依賴：handlers、HTTP、DB、AWS SDK（子類別可延遲 import 自己的依賴）。
"""
from __future__ import annotations

from typing import Any

from .concurrency import LazyModelHandle, ModelLoadUnavailable, ModelSlotPool
from .config import (
    make_provider_failure_error,
    make_provider_invalid_response_error,
    make_provider_unavailable_error,
    make_route_not_approved_error,
)
from .providers import AttemptRecord
from .types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)


class InferenceCancelled(Exception):
    """推論過程中偵測到取消。內部訊號，不外洩。"""


class InferenceDeadlineExceeded(Exception):
    """推論過程中偵測到逾期。內部訊號，不外洩。"""


def guard(cancellation: CancellationSignal, deadline: Deadline) -> None:
    """
    推論流程中的協作式檢查點。

    子類別應在每個可中斷的位置呼叫（例如逐段消費結果、送出請求前），
    讓取消與逾期有真正的生效點，而不是等整段跑完才發現沒人要這個結果。
    """
    if cancellation.is_triggered:
        raise InferenceCancelled
    if deadline.is_expired():
        raise InferenceDeadlineExceeded


class ModelProviderBase:
    """模型型 provider 的固定流程骨架。不持有任何 per-request 可變狀態。"""

    provider_id: str = "model_provider"

    def __init__(
        self,
        provider_id: str,
        slot_pool: ModelSlotPool,
        handle_wait_seconds: float,
        handle_name: str,
        load_retry_cooldown_seconds: float = 60.0,
    ) -> None:
        self.provider_id = provider_id
        self._slots = slot_pool
        self._handle_wait_seconds = handle_wait_seconds
        self._handle: LazyModelHandle[Any] = LazyModelHandle(
            name=handle_name,
            loader=self._build_handle,
            retry_cooldown_seconds=load_retry_cooldown_seconds,
        )

    # ── 供子類別實作 ──────────────────────────────────────────────
    def _build_handle(self) -> Any:
        """建立昂貴的推論 handle（模型實例或網路 client）。只會被呼叫一次。"""
        raise NotImplementedError

    def _run_inference(
        self,
        handle: Any,
        audio: CanonicalAudio,
        language: Language,
        cancellation: CancellationSignal,
        deadline: Deadline,
    ) -> str | None:
        """執行推論並回傳候選文字。非字串或空白會被正規化為無效回應。"""
        raise NotImplementedError

    def _supports(self, language: Language) -> bool:
        """這個 provider 是否服務指定語言。"""
        raise NotImplementedError

    # ── 公開介面 ─────────────────────────────────────────────────
    @property
    def slot_stats(self):
        """目前併發佔用狀態，供備援鏈與診斷使用。"""
        return self._slots.stats()

    @property
    def is_loaded(self) -> bool:
        return self._handle.is_loaded

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> Transcript | TypedAsrError:
        """AsrProvider 介面。取號等待上限由 deadline 剩餘時間決定。"""
        return self.transcribe_with_admission(
            audio,
            language,
            deadline,
            cancellation,
            context,
            max_queue_wait_seconds=deadline.remaining_seconds(),
        ).result

    def transcribe_with_admission(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
        max_queue_wait_seconds: float,
    ) -> AttemptRecord:
        """ConcurrentAsrProvider 介面：附帶是否放行與取號等待時間。"""
        if not isinstance(audio, CanonicalAudio):
            # 型別防線：provider 永遠不接受原始音訊 bytes。
            return self._reject(
                make_provider_invalid_response_error(
                    "Provider requires CanonicalAudio input."
                )
            )

        if not self._supports(language):
            return self._reject(
                make_route_not_approved_error(
                    f"Provider {self.provider_id!r} does not serve language "
                    f"{language.value!r}."
                )
            )

        # ── Preflight ──
        if cancellation.is_triggered:
            return self._reject(
                TypedAsrError(
                    category=AsrErrorCategory.CANCELLED,
                    message="Cancelled before model admission.",
                    retryable=False,
                )
            )
        if deadline.is_expired():
            return self._reject(
                TypedAsrError(
                    category=AsrErrorCategory.DEADLINE_EXCEEDED,
                    message="Deadline exceeded before model admission.",
                    retryable=True,
                )
            )

        # 等待上限同時受呼叫端 deadline 與備援政策夾住，取較小者。
        wait_budget = min(
            max(0.0, max_queue_wait_seconds), deadline.remaining_seconds()
        )

        with self._slots.lease(wait_budget) as lease:
            if not lease.acquired:
                # 飽和：沒有執行推論。admitted=False 讓備援鏈知道這是容量問題。
                return AttemptRecord(
                    provider_id=self.provider_id,
                    result=make_provider_unavailable_error(
                        f"Provider {self.provider_id!r} is at capacity."
                    ),
                    admitted=False,
                    queue_wait_ms=lease.wait_ms,
                )

            result = self._transcribe_admitted(
                audio, language, deadline, cancellation
            )
            return AttemptRecord(
                provider_id=self.provider_id,
                result=result,
                admitted=True,
                queue_wait_ms=lease.wait_ms,
            )

    # ── 內部 ─────────────────────────────────────────────────────
    def _reject(self, error: TypedAsrError) -> AttemptRecord:
        return AttemptRecord(
            provider_id=self.provider_id,
            result=error,
            admitted=False,
            queue_wait_ms=0,
        )

    def _transcribe_admitted(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> Transcript | TypedAsrError:
        """已取得 slot 後的推論流程。"""
        handle_wait = min(self._handle_wait_seconds, deadline.remaining_seconds())
        try:
            handle = self._handle.get(max_wait_seconds=handle_wait)
        except ModelLoadUnavailable:
            # handle 不可用（模型下載失敗、gated 未授權、記憶體不足、等待逾時）。
            # 這是可轉移的可用性問題，交給備援鏈換下一個 provider。
            return make_provider_unavailable_error(
                f"Model for provider {self.provider_id!r} is not available."
            )
        except Exception:
            return make_provider_failure_error(
                f"Unexpected error preparing provider {self.provider_id!r}."
            )

        try:
            candidate = self._run_inference(
                handle, audio, language, cancellation, deadline
            )
        except InferenceCancelled:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Cancelled during inference.",
                retryable=False,
            )
        except InferenceDeadlineExceeded:
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Deadline exceeded during inference.",
                retryable=True,
            )
        except TypedAsrErrorSignal as signal:
            # 子類別可以用它回報已分類的錯誤（例如 throttling → unavailable）
            # 而不必自己複製整套 postflight 邏輯。
            return signal.error
        except Exception:
            # 未分類例外：不讓原始訊息（可能含路徑、token、音訊內容）外洩。
            return make_provider_failure_error(
                f"Inference failed in provider {self.provider_id!r}."
            )

        # ── Postflight：終態不可被成功結果覆蓋 ──
        if cancellation.is_triggered:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Cancelled after inference.",
                retryable=False,
            )
        if deadline.is_expired():
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Deadline exceeded after inference.",
                retryable=True,
            )

        return self._normalize(candidate)

    @staticmethod
    def _normalize(candidate: str | None) -> Transcript | TypedAsrError:
        """非字串、空白一律視為無效回應，不猜測、不補字。"""
        if not isinstance(candidate, str):
            return make_provider_invalid_response_error(
                "Model returned a non-text result."
            )
        trimmed = candidate.strip()
        if not trimmed:
            return make_provider_invalid_response_error(
                "Model returned blank text after Unicode trim."
            )
        return Transcript(text=trimmed)


class TypedAsrErrorSignal(Exception):
    """
    子類別在推論中回報「已分類」錯誤的內部訊號。

    用途：遠端 provider 需要把 throttling、model-not-ready 這類具名錯誤映射成
    特定分類，但又要沿用骨架的 postflight 與正規化流程。
    """

    def __init__(self, error: TypedAsrError) -> None:
        super().__init__(error.category.value)
        self.error = error
