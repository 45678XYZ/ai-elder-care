"""TTS 領域單一入口。"""

from __future__ import annotations

from .router import TtsRouter
from .types import (
    CancellationSignal,
    CorrelationContext,
    Deadline,
    HakkaDialect,
    Language,
    TtsErrorCategory,
    TtsTerminalResult,
    TypedTtsError,
)


class TtsFacade:
    def __init__(self, router: TtsRouter, max_text_chars: int) -> None:
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
        if not isinstance(text, str) or not text.strip() or len(text) > self._max_text_chars:
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
