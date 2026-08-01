"""從 ASR_CONFIG_JSON 組裝 remote-only ASR facade。"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from .config import (
    AsrConfig,
    CE_MODEL_METADATA,
    ConfigParseError,
    FORMO_MODEL_METADATA,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    parse_asr_config,
)
from .facade import AsrFacade
from .providers import (
    AsrProvider,
    HakMockProvider,
    RemoteEndpointSpec,
    SageMakerAsrProvider,
)
from .router import AsrRouter
from .telemetry import SafeTelemetryRecord
from .types import Language

ENV_CONFIG_JSON = "ASR_CONFIG_JSON"
ENV_AWS_REGION = "AWS_REGION"

# 設定只能啟用經程式碼審查的模型；不接受任意 class path。
REMOTE_MODEL_LANGUAGES = {
    CE_MODEL_METADATA.model_id: frozenset({Language.ZH_TW, Language.HAK}),
    FORMO_MODEL_METADATA.model_id: frozenset({Language.HAK}),
}


class StdoutTelemetrySink:
    def emit(self, record: SafeTelemetryRecord) -> None:
        print(json.dumps({"asr_telemetry": record.to_dict()}, ensure_ascii=False))


def default_config() -> AsrConfig:
    """本機預設只允許 hak_mock；production 由 Terraform 注入明確設定。"""
    return AsrConfig(
        routes={
            "hak": RouteConfig("hak_primary", "hak_mock", True, ("ce_remote",)),
            "zh-TW": RouteConfig(
                "zh_tw_primary", "ce_remote", True, ("formo_remote",)
            ),
        },
        providers={
            "hak_mock": ProviderConfig(
                "hak_mock", ProviderStatus.ENABLED, kind=ProviderKind.MOCK
            ),
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="taiwan_tongues_ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ai-elder-care-asr-ce",
            ),
            "formo_remote": ProviderConfig(
                identifier="formo_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="formospeech_whisper_v3",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ai-elder-care-asr-formo",
            ),
        },
        model_metadata={
            "taiwan_tongues_ce": CE_MODEL_METADATA,
            "formospeech_whisper_v3": FORMO_MODEL_METADATA,
        },
    )


def load_config() -> AsrConfig:
    raw = os.environ.get(ENV_CONFIG_JSON)
    if not raw or not raw.strip():
        return default_config()
    try:
        data: Any = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigParseError(
            f"{ENV_CONFIG_JSON} is not valid JSON. Fail closed."
        ) from exc
    return parse_asr_config(data)


def build_provider_registry(config: AsrConfig) -> dict[str, AsrProvider]:
    registry: dict[str, AsrProvider] = {}
    for provider_id, provider in config.providers.items():
        if provider.status is not ProviderStatus.ENABLED:
            continue
        if provider.kind is ProviderKind.MOCK:
            if provider_id == "hak_mock":
                registry[provider_id] = HakMockProvider()
            continue
        remote = _build_remote_provider(provider_id, provider, config)
        if remote is not None:
            registry[provider_id] = remote
    return registry


def _build_remote_provider(
    provider_id: str, provider: ProviderConfig, config: AsrConfig
) -> SageMakerAsrProvider | None:
    if not provider.metadata_ref or not provider.endpoint_name:
        return None
    metadata = config.model_metadata.get(provider.metadata_ref)
    if metadata is None or not metadata.is_production_allowed:
        return None
    languages = REMOTE_MODEL_LANGUAGES.get(metadata.model_id)
    if languages is None:
        return None
    return SageMakerAsrProvider(
        provider_id,
        RemoteEndpointSpec(
            endpoint_name=provider.endpoint_name,
            model_id=metadata.model_id,
            revision=metadata.revision,
            region_name=os.environ.get(ENV_AWS_REGION) or None,
        ),
        languages,
    )


def build_facade(
    config: AsrConfig | None = None,
    providers: dict[str, AsrProvider] | None = None,
    telemetry_sink: object | None = None,
    clock: Callable[[], float] | None = None,
) -> AsrFacade:
    resolved = config or load_config()
    registry = providers if providers is not None else build_provider_registry(resolved)
    return AsrFacade(
        AsrRouter(resolved, registry),
        telemetry_sink or StdoutTelemetrySink(),  # type: ignore[arg-type]
        clock or time.monotonic,
    )


_facade: AsrFacade | None = None
_lock = threading.Lock()


def get_asr_facade() -> AsrFacade:
    global _facade
    if _facade is None:
        with _lock:
            if _facade is None:
                _facade = build_facade()
    return _facade


def reset_asr_facade() -> None:
    global _facade
    with _lock:
        _facade = None
