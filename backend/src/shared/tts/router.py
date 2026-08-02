"""設定驅動 TTS router；備援永遠限制在相同語言與腔調能力內。"""

from __future__ import annotations

from typing import Mapping

from .config import ProviderKind, ProviderStatus, TtsConfig, route_key
from .providers import TtsProvider
from .types import (
    CancellationSignal,
    CorrelationContext,
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


class TtsFacade:
    """驗證呼叫端輸入後交給設定驅動 router。"""

    def __init__(self, router: "TtsRouter", max_text_chars: int) -> None:
        self._router = router
        self._max_text_chars = max_text_chars

    def synthesize(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> TtsTerminalResult:
        del context  # correlation 只由上層安全 log；provider 不取得長者資訊。
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > self._max_text_chars
        ):
            return TypedTtsError(
                TtsErrorCategory.INVALID_TEXT,
                "TTS text is empty or exceeds the configured limit.",
                False,
            )
        if not isinstance(language, Language):
            return TypedTtsError(
                TtsErrorCategory.UNSUPPORTED_LANGUAGE,
                "Unsupported TTS language.",
                False,
            )
        if language is Language.HAK and not isinstance(dialect, HakkaDialect):
            return TypedTtsError(
                TtsErrorCategory.UNSUPPORTED_DIALECT,
                "Hakka TTS requires an approved profile dialect.",
                False,
            )
        return self._router.route(
            text, language, dialect, deadline, cancellation
        )

    def is_available(
        self, language: Language, dialect: HakkaDialect | None
    ) -> bool:
        """這輪是否會產出音訊；供非同步 TTS 在合成前決定要不要讓呼叫端等待。

        輸入驗證與 `synthesize` 相同：語言型別不對、客語缺腔調都視為不會有音訊。
        """
        if not isinstance(language, Language):
            return False
        if language is Language.HAK and not isinstance(dialect, HakkaDialect):
            return False
        return self._router.has_eligible_provider(language, dialect)


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
            result = provider.synthesize(
                text, language, dialect, deadline, cancellation
            )
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

    def has_eligible_provider(
        self, language: Language, dialect: HakkaDialect | None
    ) -> bool:
        """這個語言／腔調是否至少有一個可用 provider，不實際合成。

        非同步 TTS 需要在還沒合成前就回答呼叫端「等一下會有音訊」或「這輪不會有」。
        判定規則與 `synthesize` 完全共用，避免兩邊漂移後出現「說會有卻永遠不來」。
        """
        route = self._config.routes.get(route_key(language, dialect))
        if route is None or not route.enabled:
            return False
        return any(
            self._is_eligible(self._config.providers[provider_id], language, dialect)
            and provider_id in self._providers
            for provider_id in route.provider_order
        )

    def _is_eligible(self, provider, language, dialect) -> bool:
        if (
            provider.status is not ProviderStatus.ENABLED
            or language not in provider.languages
        ):
            return False
        if language is Language.HAK and (
            dialect is None or dialect not in provider.dialects
        ):
            return False
        if provider.kind is ProviderKind.REMOTE_MODEL:
            metadata = self._config.model_metadata.get(provider.metadata_ref or "")
            return metadata is not None and metadata.is_production_allowed
        return True
