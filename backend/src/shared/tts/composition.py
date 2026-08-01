"""TTS production composition root；只從 TTS_CONFIG_JSON 建立 provider。"""

from __future__ import annotations

import os
import threading

from .config import (
    ConfigParseError,
    ProviderKind,
    ProviderStatus,
    TtsConfig,
    disabled_config,
    parse_tts_config,
)
from .providers import MockTtsProvider, PollyTtsProvider, SageMakerTtsProvider
from .router import TtsFacade, TtsRouter

ENV_CONFIG_JSON = "TTS_CONFIG_JSON"

# 設定只能切換已經過程式碼審查的遠端模型，不接受任意 model ID。
REMOTE_MODEL_IDS = frozenset(
    {
        "formospeech/omnivoice-hakka-community-1",
        "formospeech/yourtts-htia-240704",
        "MediaTek-Research/BreezyVoice",
    }
)

_facade: TtsFacade | None = None
_lock = threading.Lock()


def load_config() -> TtsConfig:
    raw = os.environ.get(ENV_CONFIG_JSON)
    return parse_tts_config(raw) if raw else disabled_config()


def build_provider_registry(config: TtsConfig) -> dict[str, object]:
    registry: dict[str, object] = {}
    for provider_id, provider_config in config.providers.items():
        if provider_config.status is not ProviderStatus.ENABLED:
            continue
        if provider_config.kind is ProviderKind.REMOTE_MODEL:
            metadata = config.model_metadata.get(provider_config.metadata_ref or "")
            if (
                metadata is not None
                and metadata.model_id in REMOTE_MODEL_IDS
                and metadata.is_production_allowed
            ):
                registry[provider_id] = SageMakerTtsProvider(provider_config)
        elif provider_config.kind is ProviderKind.AWS_MANAGED:
            registry[provider_id] = PollyTtsProvider(provider_config)
        elif provider_config.kind is ProviderKind.MOCK and provider_id == "tts_mock":
            registry[provider_id] = MockTtsProvider(provider_id)
    return registry


def build_facade(
    config: TtsConfig | None = None, providers: dict[str, object] | None = None
) -> TtsFacade:
    resolved = config or load_config()
    registry = providers if providers is not None else build_provider_registry(resolved)
    return TtsFacade(TtsRouter(resolved, registry), resolved.max_text_chars)


def get_tts_facade() -> TtsFacade:
    global _facade
    if _facade is None:
        with _lock:
            if _facade is None:
                _facade = build_facade()
    return _facade


def reset_tts_facade_for_tests() -> None:
    global _facade
    with _lock:
        _facade = None
