"""TTS 後端受控設定；未知或未核准內容一律 fail closed。"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Any

from .types import HakkaDialect, Language


class ConfigParseError(ValueError):
    """TTS_CONFIG_JSON 不符合受控 schema。"""


class ProviderKind(enum.Enum):
    MOCK = "mock"
    AWS_MANAGED = "aws_managed"
    REMOTE_MODEL = "remote_model"


class ProviderStatus(enum.Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ModelProductionGate:
    """遠端模型上線前須由團隊逐項確認的 production gate。"""

    staging_validation_passed: bool = False
    license_cleared: bool = False
    access_granted: bool = False
    quota_cleared: bool = False
    runtime_capacity_verified: bool = False
    latency_slo_verified: bool = False
    approval_record_ref: str | None = None

    @property
    def is_approved(self) -> bool:
        return all(
            (
                self.staging_validation_passed,
                self.license_cleared,
                self.access_granted,
                self.quota_cleared,
                self.runtime_capacity_verified,
                self.latency_slo_verified,
            )
        )


@dataclass(frozen=True)
class ModelMetadata:
    model_id: str
    revision: str
    license: str
    approved_for_production: bool
    production_gate: ModelProductionGate

    @property
    def is_production_allowed(self) -> bool:
        return self.approved_for_production and self.production_gate.is_approved


@dataclass(frozen=True)
class ProviderConfig:
    identifier: str
    kind: ProviderKind
    status: ProviderStatus
    languages: frozenset[Language]
    dialects: frozenset[HakkaDialect] = frozenset()
    metadata_ref: str | None = None
    endpoint_name: str | None = None
    voice_id: str | None = None
    engine: str | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class RouteConfig:
    route: str
    enabled: bool
    provider_identifier: str
    fallback_chain: tuple[str, ...] = ()

    @property
    def provider_order(self) -> tuple[str, ...]:
        return (self.provider_identifier, *self.fallback_chain)


@dataclass(frozen=True)
class TtsConfig:
    schema_version: int
    providers: dict[str, ProviderConfig]
    routes: dict[str, RouteConfig]
    model_metadata: dict[str, ModelMetadata]
    max_text_chars: int = 3000
    max_audio_bytes: int = 10 * 1024 * 1024


def route_key(language: Language, dialect: HakkaDialect | None = None) -> str:
    """客語必須帶 profile 腔調；中文只使用語言 route。"""
    if language is Language.HAK:
        return f"{language.value}:{dialect.value}" if dialect else language.value
    return language.value


def parse_tts_config(raw: str | dict[str, Any]) -> TtsConfig:
    """解析嚴格的 TTS schema v1，不以環境或文字內容猜測 provider。"""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ConfigParseError("TTS_CONFIG_JSON must be valid JSON.") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ConfigParseError("TTS config requires schema_version=1.")

    providers_raw = _mapping(data, "providers")
    routes_raw = _mapping(data, "routes")
    metadata_raw = data.get("model_metadata", {})
    if not isinstance(metadata_raw, dict):
        raise ConfigParseError("model_metadata must be an object.")

    metadata = {key: _parse_metadata(key, value) for key, value in metadata_raw.items()}
    providers = {key: _parse_provider(key, value) for key, value in providers_raw.items()}
    routes = {key: _parse_route(key, value) for key, value in routes_raw.items()}

    for provider in providers.values():
        if provider.kind is ProviderKind.REMOTE_MODEL:
            if not provider.endpoint_name or not provider.metadata_ref:
                raise ConfigParseError(
                    f"remote provider {provider.identifier!r} requires endpoint_name and metadata_ref."
                )
            if provider.metadata_ref not in metadata:
                raise ConfigParseError(
                    f"provider {provider.identifier!r} references unknown metadata."
                )
        if provider.kind is ProviderKind.AWS_MANAGED and (
            not provider.voice_id or provider.engine not in {"neural", "standard"}
        ):
            raise ConfigParseError(
                f"AWS provider {provider.identifier!r} requires voice_id and neural|standard engine."
            )

    for key, route in routes.items():
        if len(set(route.provider_order)) != len(route.provider_order):
            raise ConfigParseError(f"route {key!r} repeats a provider.")
        if any(provider_id not in providers for provider_id in route.provider_order):
            raise ConfigParseError(f"route {key!r} references an unknown provider.")

    return TtsConfig(
        schema_version=1,
        providers=providers,
        routes=routes,
        model_metadata=metadata,
        max_text_chars=_positive_int(data, "max_text_chars", 3000),
        max_audio_bytes=_positive_int(data, "max_audio_bytes", 10 * 1024 * 1024),
    )


def disabled_config() -> TtsConfig:
    """未注入設定時沒有可用 route，避免意外啟用付費或未核准服務。"""
    return TtsConfig(schema_version=1, providers={}, routes={}, model_metadata={})


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigParseError(f"{key} must be an object.")
    return value


def _parse_provider(identifier: str, value: Any) -> ProviderConfig:
    if not isinstance(identifier, str) or not identifier.strip() or not isinstance(value, dict):
        raise ConfigParseError("provider entries must be named objects.")
    raw_languages = value.get("languages")
    raw_dialects = value.get("dialects", [])
    if not isinstance(raw_languages, list) or not isinstance(raw_dialects, list):
        raise ConfigParseError(
            f"provider {identifier!r} languages and dialects must be lists."
        )
    try:
        languages = frozenset(Language(item) for item in raw_languages)
        dialects = frozenset(HakkaDialect(item) for item in raw_dialects)
        provider = ProviderConfig(
            identifier=identifier,
            kind=ProviderKind(value["kind"]),
            status=ProviderStatus(value["status"]),
            languages=languages,
            dialects=dialects,
            metadata_ref=value.get("metadata_ref"),
            endpoint_name=value.get("endpoint_name"),
            voice_id=value.get("voice_id"),
            engine=value.get("engine"),
            speaker=value.get("speaker"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigParseError(f"invalid provider {identifier!r}.") from exc
    if not languages:
        raise ConfigParseError(f"provider {identifier!r} requires languages.")
    return provider


def _parse_route(key: str, value: Any) -> RouteConfig:
    if not isinstance(key, str) or not key.strip() or not isinstance(value, dict):
        raise ConfigParseError("route entries must be named objects.")
    raw_fallback = value.get("fallback_chain", [])
    if not isinstance(raw_fallback, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_fallback
    ):
        raise ConfigParseError(f"route {key!r} fallback_chain must be a string list.")
    if not isinstance(value.get("enabled"), bool):
        raise ConfigParseError(f"route {key!r} enabled must be bool.")
    if not isinstance(value.get("route"), str) or not value["route"].strip():
        raise ConfigParseError(f"route {key!r} route must be a non-blank string.")
    if not isinstance(value.get("provider_identifier"), str) or not value[
        "provider_identifier"
    ].strip():
        raise ConfigParseError(
            f"route {key!r} provider_identifier must be a non-blank string."
        )
    try:
        fallback = tuple(raw_fallback)
        return RouteConfig(
            route=value["route"],
            enabled=value["enabled"],
            provider_identifier=value["provider_identifier"],
            fallback_chain=fallback,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigParseError(f"invalid route {key!r}.") from exc


def _parse_metadata(key: str, value: Any) -> ModelMetadata:
    if not isinstance(value, dict):
        raise ConfigParseError(f"metadata {key!r} must be an object.")
    gate_raw = value.get("production_gate", {})
    if not isinstance(gate_raw, dict):
        raise ConfigParseError(f"metadata {key!r} production_gate must be an object.")
    approval_record_ref = gate_raw.get("approval_record_ref")
    if approval_record_ref is not None and not isinstance(approval_record_ref, str):
        raise ConfigParseError(
            f"metadata {key!r} approval_record_ref must be string or null."
        )
    try:
        gate = ModelProductionGate(
            staging_validation_passed=gate_raw.get("staging_validation_passed") is True,
            license_cleared=gate_raw.get("license_cleared") is True,
            access_granted=gate_raw.get("access_granted") is True,
            quota_cleared=gate_raw.get("quota_cleared") is True,
            runtime_capacity_verified=gate_raw.get("runtime_capacity_verified") is True,
            latency_slo_verified=gate_raw.get("latency_slo_verified") is True,
            approval_record_ref=approval_record_ref,
        )
        model_id = value["model_id"]
        revision = value["revision"]
        license_name = value["license"]
        if not all(
            isinstance(item, str) and item.strip()
            for item in (model_id, revision, license_name)
        ):
            raise ValueError("metadata strings must be non-blank")
        return ModelMetadata(
            model_id=model_id,
            revision=revision,
            license=license_name,
            approved_for_production=value.get("approved_for_production") is True,
            production_gate=gate,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigParseError(f"invalid metadata {key!r}.") from exc


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigParseError(f"{key} must be a positive integer.")
    return value
