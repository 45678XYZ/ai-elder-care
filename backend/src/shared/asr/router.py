"""設定驅動的 ASR 路由與同語言 fallback。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .config import (
    AsrConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    make_provider_failure_error,
    make_route_not_approved_error,
    make_unsupported_language_error,
)
from .providers import AsrProvider, HakMockProvider
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    HakkaDialect,
    Language,
    Transcript,
    TypedAsrError,
)

NOT_ROUTED_SENTINEL = "__not_routed__"
_FAILOVER_CATEGORIES = frozenset(
    {
        AsrErrorCategory.PROVIDER_UNAVAILABLE,
        AsrErrorCategory.PROVIDER_FAILURE,
        AsrErrorCategory.PROVIDER_INVALID_RESPONSE,
    }
)


@dataclass(frozen=True)
class RouteOutcome:
    """Router 終態與低基數遙測資料。"""

    result: AsrTerminalResult
    attempted_provider_ids: tuple[str, ...] = ()
    served_provider_id: str = NOT_ROUTED_SENTINEL

    @property
    def attempt_count(self) -> int:
        return len(self.attempted_provider_ids)

    @property
    def failover_occurred(self) -> bool:
        return self.attempt_count > 1


def route_key(language: Language, dialect: HakkaDialect | None = None) -> str:
    """客語優先使用 profile 腔調 route；中文只使用語言 route。"""
    if language is Language.HAK and dialect is not None:
        return f"{language.value}:{dialect.value}"
    return language.value


class AsrRouter:
    """驗證核准 gate 後，依設定順序直接呼叫 provider。"""

    def __init__(
        self,
        config: AsrConfig,
        providers: Mapping[str, AsrProvider] | None = None,
    ) -> None:
        self._config = config
        self._providers: dict[str, AsrProvider] = {"hak_mock": HakMockProvider()}
        if providers:
            self._providers.update(providers)

    @property
    def config(self) -> AsrConfig:
        return self._config

    def route_config_for(
        self, language: Language, hakka_dialect: HakkaDialect | None = None
    ) -> RouteConfig | None:
        if not isinstance(language, Language):
            return None
        return self._config.routes.get(
            route_key(language, hakka_dialect)
        ) or self._config.routes.get(language.value)

    def route(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
        hakka_dialect: HakkaDialect | None = None,
    ) -> AsrTerminalResult:
        return self.route_detailed(
            audio, language, deadline, cancellation, context, hakka_dialect
        ).result

    def route_detailed(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
        hakka_dialect: HakkaDialect | None = None,
    ) -> RouteOutcome:
        if not isinstance(language, Language):
            return RouteOutcome(make_unsupported_language_error(str(language)))

        key = route_key(language, hakka_dialect)
        route = self._config.routes.get(key)
        if route is None:
            key = language.value
            route = self._config.routes.get(key)
        if route is None or not route.enabled:
            return RouteOutcome(
                make_route_not_approved_error(
                    f"No approved ASR route for {key!r}."
                )
            )

        providers = self._eligible_providers(route)
        if not providers:
            return RouteOutcome(
                make_route_not_approved_error(
                    f"No approved ASR provider for {key!r}."
                )
            )

        attempted: list[str] = []
        last_error: TypedAsrError | None = None
        for provider_id, provider in providers:
            guard = _guard(deadline, cancellation)
            if guard is not None:
                return RouteOutcome(guard, tuple(attempted), provider_id)

            attempted.append(provider_id)
            try:
                result = provider.transcribe(
                    audio, language, deadline, cancellation, context
                )
            except Exception:
                result = make_provider_failure_error(
                    f"Provider {provider_id!r} raised an unexpected error."
                )

            if isinstance(result, Transcript):
                return RouteOutcome(result, tuple(attempted), provider_id)
            if not isinstance(result, TypedAsrError):
                result = make_provider_failure_error(
                    f"Provider {provider_id!r} returned a non-terminal result."
                )
            last_error = result
            if result.category not in _FAILOVER_CATEGORIES:
                return RouteOutcome(result, tuple(attempted), provider_id)

        assert last_error is not None
        return RouteOutcome(last_error, tuple(attempted), attempted[-1])

    def _eligible_providers(
        self, route: RouteConfig
    ) -> list[tuple[str, AsrProvider]]:
        eligible: list[tuple[str, AsrProvider]] = []
        for position, provider_id in enumerate(route.provider_order):
            config = self._config.providers.get(provider_id)
            provider = self._providers.get(provider_id)
            if config is None or provider is None:
                if position == 0:
                    return []
                continue
            if config.status is not ProviderStatus.ENABLED:
                if position == 0:
                    return []
                continue
            if config.kind is ProviderKind.REMOTE_MODEL:
                if not config.endpoint_name or not config.metadata_ref:
                    if position == 0:
                        return []
                    continue
                metadata = self._config.model_metadata.get(config.metadata_ref)
                if metadata is None or not metadata.is_production_allowed:
                    if position == 0:
                        return []
                    continue
            elif config.kind is ProviderKind.AWS_MANAGED:
                if config.endpoint_name is not None or config.metadata_ref is not None:
                    if position == 0:
                        return []
                    continue
            eligible.append((provider_id, provider))
        return eligible


def _guard(
    deadline: Deadline, cancellation: CancellationSignal
) -> TypedAsrError | None:
    if cancellation.is_triggered:
        return TypedAsrError(
            AsrErrorCategory.CANCELLED,
            "Cancelled before provider invocation.",
            False,
        )
    if deadline.is_expired():
        return TypedAsrError(
            AsrErrorCategory.DEADLINE_EXCEEDED,
            "Deadline exceeded before provider invocation.",
            True,
        )
    return None
