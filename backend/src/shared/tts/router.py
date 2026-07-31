"""設定驅動 TTS router；備援永遠限制在相同語言與腔調能力內。"""

from __future__ import annotations

from typing import Mapping

from .config import ProviderKind, ProviderStatus, TtsConfig, route_key
from .providers import TtsProvider
from .types import (
    CancellationSignal,
    Deadline,
    HakkaDialect,
    Language,
    SynthesizedAudio,
    TtsErrorCategory,
    TtsTerminalResult,
    TypedTtsError,
)

_FAILOVER_CATEGORIES = frozenset(
    {
        TtsErrorCategory.PROVIDER_UNAVAILABLE,
        TtsErrorCategory.PROVIDER_FAILURE,
        TtsErrorCategory.INVALID_RESPONSE,
    }
)


class TtsRouter:
    def __init__(self, config: TtsConfig, providers: Mapping[str, TtsProvider]) -> None:
        self._config = config
        self._providers = providers

    def route(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> TtsTerminalResult:
        key = route_key(language, dialect)
        route = self._config.routes.get(key)
        if route is None or not route.enabled:
            return TypedTtsError(
                TtsErrorCategory.ROUTE_NOT_APPROVED,
                f"No approved TTS route for {key!r}.",
                False,
            )

        last_error: TypedTtsError | None = None
        attempted = False
        for provider_id in route.provider_order:
            provider_config = self._config.providers[provider_id]
            if not self._is_eligible(provider_config, language, dialect):
                continue
            provider = self._providers.get(provider_id)
            if provider is None:
                continue
            attempted = True
            result = provider.synthesize(text, language, dialect, deadline, cancellation)
            if isinstance(result, SynthesizedAudio):
                if len(result.data) > self._config.max_audio_bytes:
                    result = TypedTtsError(
                        TtsErrorCategory.INVALID_RESPONSE,
                        f"TTS provider {provider_id!r} returned oversized audio.",
                        True,
                    )
                else:
                    return result
            last_error = result
            if result.category not in _FAILOVER_CATEGORIES:
                return result

        if attempted and last_error is not None:
            return last_error
        return TypedTtsError(
            TtsErrorCategory.ROUTE_NOT_APPROVED,
            f"No eligible TTS provider for {key!r}.",
            False,
        )

    def _is_eligible(self, provider, language, dialect) -> bool:
        if provider.status is not ProviderStatus.ENABLED or language not in provider.languages:
            return False
        if language is Language.HAK and (
            dialect is None or dialect not in provider.dialects
        ):
            return False
        if provider.kind is ProviderKind.REMOTE_MODEL:
            metadata = self._config.model_metadata.get(provider.metadata_ref or "")
            return metadata is not None and metadata.is_production_allowed
        return True
