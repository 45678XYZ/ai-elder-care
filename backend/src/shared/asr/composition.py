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
    AMAZON_TRANSCRIBE_PROVIDER_ID,
    AmazonTranscribeAsrProvider,
    AsrProvider,
    HakMockProvider,
    RemoteEndpointSpec,
    SageMakerAsrProvider,
)
from .router import AsrRouter
from .telemetry import SafeTelemetryRecord
from .types import Language
from src.shared.config_source import load_raw_config

ENV_CONFIG_JSON = "ASR_CONFIG_JSON"
# 六腔全開的設定放不進 Lambda 的 4 KB 環境變數上限，改由 SSM 提供（見 config_source）。
ENV_CONFIG_PARAMETER = "ASR_CONFIG_SSM_PARAMETER"
ENV_AWS_REGION = "AWS_REGION"

# 設定只能啟用經程式碼審查的模型；不接受任意 class path。
REMOTE_MODEL_LANGUAGES = {
    CE_MODEL_METADATA.model_id: frozenset({Language.ZH_TW, Language.HAK}),
    FORMO_MODEL_METADATA.model_id: frozenset({Language.HAK}),
}
AWS_MANAGED_PROVIDER_LANGUAGES = {
    AMAZON_TRANSCRIBE_PROVIDER_ID: frozenset({Language.ZH_TW}),
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
                "zh_tw_primary",
                AMAZON_TRANSCRIBE_PROVIDER_ID,
                True,
                ("ce_remote",),
            ),
        },
        providers={
            "hak_mock": ProviderConfig(
                "hak_mock", ProviderStatus.ENABLED, kind=ProviderKind.MOCK
            ),
            AMAZON_TRANSCRIBE_PROVIDER_ID: ProviderConfig(
                identifier=AMAZON_TRANSCRIBE_PROVIDER_ID,
                status=ProviderStatus.DISABLED,
                kind=ProviderKind.AWS_MANAGED,
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
    raw = load_raw_config(ENV_CONFIG_JSON, ENV_CONFIG_PARAMETER)
    if raw is None:
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
        if provider.kind is ProviderKind.AWS_MANAGED:
            managed = _build_aws_managed_provider(provider_id, provider)
            if managed is not None:
                registry[provider_id] = managed
            continue
        remote = _build_remote_provider(provider_id, provider, config)
        if remote is not None:
            registry[provider_id] = remote
    return registry


def _build_aws_managed_provider(
    provider_id: str, provider: ProviderConfig
) -> AmazonTranscribeAsrProvider | None:
    """只允許程式碼明列的 AWS managed adapter，拒絕任意服務名稱。"""
    if (
        provider_id not in AWS_MANAGED_PROVIDER_LANGUAGES
        or provider.identifier != provider_id
        or provider.metadata_ref is not None
        or provider.endpoint_name is not None
    ):
        return None
    return AmazonTranscribeAsrProvider(
        provider_id=provider_id,
        region_name=os.environ.get(ENV_AWS_REGION) or None,
    )


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
