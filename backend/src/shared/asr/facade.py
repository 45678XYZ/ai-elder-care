"""
ASR Facade — ASR 領域套件的單一入口。

只接收六個輸入（audio_bytes、input_format、language、deadline、cancellation、context），
依序協調 input gate → canonicalizer → router → provider，
只回傳非空白 Transcript 或 TypedAsrError。

每個 correlation context 產生恰一筆 terminal telemetry。
不理解 HTTP、資料庫或對話工作流。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

from typing import Callable

from .canonical_audio import canonicalize
from .router import AsrRouter
from .telemetry import TelemetrySink, TerminalTelemetryEmitter
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


class AsrFacade:
    """
    ASR 領域套件的單一入口 Facade。

    接收 6 個輸入：audio_bytes、input_format、language、deadline、
    cancellation、context。

    依序協調：input gate → canonicalizer → router（含備援鏈與 provider）。
    回傳 Transcript 或 TypedAsrError。

    每個 recognize 呼叫產生恰一筆 terminal telemetry，不理解 HTTP、資料庫
    或對話工作流。

    **可被多執行緒同時呼叫**：facade 只持有 router、telemetry sink 與 clock
    三個唯讀依賴，每次呼叫自建 emitter；實體模型的併發上限由各 provider
    自己的 slot pool 把關。
    """

    def __init__(
        self,
        router: AsrRouter,
        telemetry_sink: TelemetrySink,
        clock: Callable[[], float],
    ) -> None:
        """
        初始化 AsrFacade。

        Args:
            router: ASR Router 實例（含設定、provider）。
            telemetry_sink: telemetry 接收端（injectable local interface）。
            clock: injected monotonic clock（Callable[[], float]）。
        """
        self._router = router
        self._telemetry_sink = telemetry_sink
        self._clock = clock

    def recognize(
        self,
        audio_bytes: bytes,
        input_format: InputFormat,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult:
        """
        執行一次 ASR 辨識。

        Input gate → Canonicalize → Route → Provider。
        產生恰一筆 terminal telemetry。

        Args:
            audio_bytes: 原始音訊位元組。
            input_format: 宣告的來源音訊格式（wav/m4a）。
            language: 辨識語言（zh-TW/hak）。
            deadline: 單調時鐘到期時刻。
            cancellation: 協作式取消訊號。
            context: 呼叫關聯資訊。

        Returns:
            Transcript（成功）或 TypedAsrError（失敗）。
        """
        # 建立 monotonic start time 與 telemetry emitter
        start_time = self._clock()
        emitter = TerminalTelemetryEmitter(
            sink=self._telemetry_sink,
            clock=self._clock,
            start_time=start_time,
            correlation_id=context.correlation_id,
        )

        # 設定已知 metadata 到 emitter
        emitter.set_language(language)
        emitter.set_input_format(input_format)

        # ─── Input Gate ───
        result = self._input_gate(audio_bytes, context)
        if result is not None:
            emitter.emit(result)
            return result

        # ─── Canonicalize ───
        canon_result = canonicalize(audio_bytes, input_format)

        if isinstance(canon_result, TypedAsrError):
            emitter.emit(canon_result)
            return canon_result

        # canonicalize 成功 — 設定 canonical audio metadata
        canonical_audio: CanonicalAudio = canon_result
        emitter.set_canonical_audio(canonical_audio)

        # ─── Route → Provider ───
        # 先以設定的 route 名稱填 telemetry，實際服務的 provider 在鏈跑完後覆寫，
        # 因為備援可能讓最終處理者不是設定中的主 provider。
        route_config = self._router.route_config_for(language)
        if route_config is not None:
            emitter.set_route(route_config.route)
            emitter.set_provider_id(route_config.provider_identifier)

        outcome = self._router.route_detailed(
            audio=canonical_audio,
            language=language,
            deadline=deadline,
            cancellation=cancellation,
            context=context,
        )

        if outcome.attempts:
            emitter.set_provider_id(outcome.served_provider_id)
        emitter.set_chain_metrics(
            attempt_count=outcome.attempt_count,
            queue_wait_ms=outcome.total_queue_wait_ms,
            failover_occurred=outcome.failover_occurred,
        )

        emitter.emit(outcome.result)
        return outcome.result

    @staticmethod
    def _input_gate(
        audio_bytes: bytes,
        context: CorrelationContext,
    ) -> TypedAsrError | None:
        """
        Input gate 驗證。

        - 空/blank audio_bytes → invalid_audio
        - CorrelationContext 驗證已由 dataclass __post_init__ 保證，
          但若 context 本身不是 CorrelationContext 也拒絕。

        Returns:
            None 表示通過；TypedAsrError 表示被拒絕。
        """
        # Empty/blank audio_bytes
        if not audio_bytes:
            return TypedAsrError(
                category=AsrErrorCategory.INVALID_AUDIO,
                message="Empty audio bytes.",
                retryable=False,
            )

        # CorrelationContext validation —
        # CorrelationContext dataclass 本身已做 __post_init__ 驗證，
        # 但防禦性檢查 context 型別
        if not isinstance(context, CorrelationContext):
            return TypedAsrError(
                category=AsrErrorCategory.INVALID_AUDIO,
                message="Invalid or missing correlation context.",
                retryable=False,
            )

        return None
