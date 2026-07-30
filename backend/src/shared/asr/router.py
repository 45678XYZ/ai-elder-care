"""
ASR Router — 固定 precedence 的路由決策與備援鏈建構。

判定順序（不可調換）：

    1. language validity        未知語言 → unsupported_language
    2. route 存在且啟用          缺失／停用 → route_not_approved
    3. provider 核准資格         沒有任何合格 provider → route_not_approved
    4. cancellation             已取消 → cancelled
    5. deadline                 已逾期 → deadline_exceeded
    6. 備援鏈依序執行

第 3 步排在取消與逾期之前是刻意的：核准是管理決策，operator 在排查一條被關閉的
路由時，永遠應該看到 route_not_approved，而不是被當下的取消或逾期蓋掉。

provider 由外部注入（composition root 負責建立），router 不自行建構任何需要
模型、網路或雲端資源的物件。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

from typing import Mapping

from .config import (
    AsrConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    make_route_not_approved_error,
    make_unsupported_language_error,
)
from .failover import ChainOutcome, FailoverChain
from .hak_mock import HakMockProvider
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    TypedAsrError,
)

# 未路由時 telemetry 使用的固定 sentinel，與 telemetry.py 一致。
NOT_ROUTED_SENTINEL = "__not_routed__"


class AsrRouter:
    """
    設定驅動的 ASR 路由器。

    可被多執行緒同時呼叫：本身只讀設定與 provider registry，不持有 per-request
    狀態。併發上限由各 provider 自己的 slot pool 把關。
    """

    def __init__(
        self,
        config: AsrConfig,
        providers: Mapping[str, object] | None = None,
    ) -> None:
        """
        Args:
            config: 後端受控設定。
            providers: provider registry（identifier → provider 實例）。
                未提供的項目會以安全預設補上：`hak_mock` 不會建立模型、網路或
                雲端呼叫，因此可安全地內建。實體模型 provider 一律由
                composition root 注入，router 不自行建構。
        """
        self._config = config
        registry: dict[str, object] = {
            "hak_mock": HakMockProvider(),
        }
        if providers:
            registry.update(providers)
        self._providers = registry

    # ── 公開讀取介面（供 facade 取得 telemetry 需要的路由資訊）──────────
    @property
    def config(self) -> AsrConfig:
        return self._config

    def route_config_for(self, language: Language) -> RouteConfig | None:
        """取得語言對應的 route 設定；未設定回 None。"""
        if not isinstance(language, Language):
            return None
        return self._config.routes.get(language.value)

    # ── 路由 ────────────────────────────────────────────────────────
    def route(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult:
        """回傳終態結果。需要嘗試明細時用 `route_detailed`。"""
        return self.route_detailed(
            audio, language, deadline, cancellation, context
        ).result

    def route_detailed(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> ChainOutcome:
        """回傳含每次 provider 嘗試明細的 ChainOutcome。"""
        # ─── Step 1: Language validity ───
        if not isinstance(language, Language):
            return _terminal(
                make_unsupported_language_error(str(language))
            )

        # ─── Step 2: Route 存在且啟用 ───
        route_config = self._config.routes.get(language.value)
        if route_config is None:
            return _terminal(
                make_route_not_approved_error(
                    f"No route configured for {language.value!r}."
                )
            )
        if not route_config.enabled:
            return _terminal(
                make_route_not_approved_error(
                    f"Route for {language.value!r} is disabled."
                )
            )

        # ─── Step 3: Provider 核准資格 ───
        eligible, rejection = self._eligible_providers(route_config)
        if not eligible:
            return _terminal(
                make_route_not_approved_error(
                    rejection
                    or f"No approved provider for {language.value!r}."
                )
            )

        # ─── Step 4: Cancellation ───
        if cancellation.is_triggered:
            return _terminal(
                TypedAsrError(
                    category=AsrErrorCategory.CANCELLED,
                    message="Cancelled before provider invocation.",
                    retryable=False,
                ),
                served_provider_id=route_config.provider_identifier,
            )

        # ─── Step 5: Deadline ───
        if deadline.is_expired():
            return _terminal(
                TypedAsrError(
                    category=AsrErrorCategory.DEADLINE_EXCEEDED,
                    message="Deadline exceeded before provider invocation.",
                    retryable=True,
                ),
                served_provider_id=route_config.provider_identifier,
            )

        # ─── Step 6: 備援鏈 ───
        chain = FailoverChain(
            providers=eligible,
            spill_wait_seconds=self._config.concurrency.spill_wait_seconds,
        )
        return chain.run(audio, language, deadline, cancellation, context)

    # ── 資格判定 ─────────────────────────────────────────────────────
    def _eligible_providers(
        self, route_config: RouteConfig
    ) -> tuple[list[object], str | None]:
        """
        依 provider_order 篩出可用的 provider 實例。

        Returns:
            (合格 provider 實例清單, 全部不合格時的安全原因說明)
        """
        eligible: list[object] = []
        reasons: list[str] = []

        for provider_id in route_config.provider_order:
            reason = self._ineligibility_reason(provider_id)
            if reason is None:
                eligible.append(self._providers[provider_id])
            else:
                reasons.append(f"{provider_id}: {reason}")

        if eligible:
            return eligible, None
        return [], "; ".join(reasons) if reasons else None

    def _ineligibility_reason(self, provider_id: str) -> str | None:
        """回傳不合格原因；合格回 None。訊息不含敏感內容。"""
        provider_config = self._config.providers.get(provider_id)
        if provider_config is None:
            return "not declared in provider configuration"
        if provider_config.status is not ProviderStatus.ENABLED:
            return f"status is {provider_config.status.value}"
        if provider_id not in self._providers:
            return "no provider instance registered"

        kind = provider_config.kind

        if kind.requires_model_approval and not provider_config.metadata_ref:
            return f"{kind.value} provider has no model metadata reference"

        if kind is ProviderKind.REMOTE_MODEL and not provider_config.endpoint_name:
            return "remote model provider has no endpoint name"

        if provider_config.metadata_ref:
            metadata = self._config.model_metadata.get(provider_config.metadata_ref)
            if metadata is None:
                return "referenced model metadata is missing"
            if not metadata.is_production_allowed:
                missing = list(metadata.production_gate.missing_items)
                return (
                    "model production gate not approved "
                    f"(usage={metadata.usage_restriction.value}, "
                    f"approval={metadata.approval_state.value}, "
                    f"missing={missing})"
                )

        return None


def _terminal(
    error: TypedAsrError,
    served_provider_id: str = NOT_ROUTED_SENTINEL,
) -> ChainOutcome:
    """把 router 層的終態包成沒有任何 provider 嘗試的 ChainOutcome。"""
    return ChainOutcome(
        result=error,
        attempts=(),
        served_provider_id=served_provider_id,
        total_queue_wait_ms=0,
    )
