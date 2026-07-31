"""可切換中文／客語遠端模型的 TTS 領域套件。"""

from .composition import build_facade, get_tts_facade
from .config import ConfigParseError, parse_tts_config
from .facade import TtsFacade
from .types import (
    CancellationSignal,
    CorrelationContext,
    Deadline,
    HakkaDialect,
    Language,
    SynthesizedAudio,
    TtsErrorCategory,
    TypedTtsError,
)

__all__ = [
    "CancellationSignal",
    "ConfigParseError",
    "CorrelationContext",
    "Deadline",
    "HakkaDialect",
    "Language",
    "SynthesizedAudio",
    "TtsErrorCategory",
    "TtsFacade",
    "TypedTtsError",
    "build_facade",
    "get_tts_facade",
    "parse_tts_config",
]
