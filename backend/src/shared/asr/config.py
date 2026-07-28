"""
ASR 後端受控設定：route、provider、model metadata、AWS capability gate 與 Formo Prompt ID validator。

解析失敗、未知 schema version、缺必填鍵或相互矛盾的狀態一律 fail closed。

禁止依賴：handlers、HTTP、DB、AWS SDK、環境自動選服務/Region。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, FrozenSet

from .types import AsrErrorCategory, TypedAsrError


# ─────────────────────────────────────────────────────────────────
# Formo Prompt ID — 精確 allowlist
# ─────────────────────────────────────────────────────────────────
_FORMO_PROMPT_ID_ALLOWLIST: FrozenSet[str] = frozenset(
    {
        "htia_sixian",
        "htia_hailu",
        "htia_dapu",
        "htia_raoping",
        "htia_zhaoan",
        "htia_nansixian",
    }
)


def validate_formo_prompt_id(candidate: str) -> str:
    """
    驗證 Formo Prompt ID。

    僅接受精確等於六個允許值的字串。
    拒絕空白、大小寫變形、前後空白與 Unicode lookalike。
    不做任何正規化或猜測。

    Returns:
        精確匹配的 prompt ID 值。

    Raises:
        ValueError: 如果 candidate 不在 allowlist。
    """
    if not isinstance(candidate, str):
        raise ValueError(f"Formo Prompt ID must be a string, got {type(candidate).__name__}.")
    # 不做 strip、lower、NFKC 或任何正規化 — 精確比對
    if candidate not in _FORMO_PROMPT_ID_ALLOWLIST:
        raise ValueError(
            f"Formo Prompt ID rejected: {candidate!r}. "
            f"Must be exactly one of: {sorted(_FORMO_PROMPT_ID_ALLOWLIST)}."
        )
    return candidate


# ─────────────────────────────────────────────────────────────────
# 用途限制
# ─────────────────────────────────────────────────────────────────
class UsageRestriction(enum.Enum):
    """模型用途限制。"""

    COLAB_VALIDATION_ONLY = "colab_validation_only"
    PRODUCTION = "production"


# ─────────────────────────────────────────────────────────────────
# 存取狀態
# ─────────────────────────────────────────────────────────────────
class AccessStatus(enum.Enum):
    """模型存取狀態。"""

    OPEN = "open"
    GATED = "gated"
    RESTRICTED = "restricted"


# ─────────────────────────────────────────────────────────────────
# 核准狀態
# ─────────────────────────────────────────────────────────────────
class ApprovalState(enum.Enum):
    """核准狀態。"""

    NOT_APPROVED = "not_approved"
    APPROVED = "approved"
    PENDING = "pending"


# ─────────────────────────────────────────────────────────────────
# Provider 狀態
# ─────────────────────────────────────────────────────────────────
class ProviderStatus(enum.Enum):
    """Provider 狀態。"""

    ENABLED = "enabled"
    DISABLED = "disabled"
    COLAB_ONLY = "colab_only"


# ─────────────────────────────────────────────────────────────────
# Model Metadata — 不可變
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelMetadata:
    """
    模型中繼資料。

    保存 model ID、version/revision、license、access status、usage restriction 與 approval state。
    """

    model_id: str
    revision: str
    license: str
    access_status: AccessStatus
    usage_restriction: UsageRestriction
    approval_state: ApprovalState

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_id.strip():
            raise ValueError("ModelMetadata.model_id must be non-blank.")
        if not self.revision or not self.revision.strip():
            raise ValueError("ModelMetadata.revision must be non-blank.")
        if not self.license or not self.license.strip():
            raise ValueError("ModelMetadata.license must be non-blank.")
        if not isinstance(self.access_status, AccessStatus):
            raise TypeError("ModelMetadata.access_status must be AccessStatus.")
        if not isinstance(self.usage_restriction, UsageRestriction):
            raise TypeError("ModelMetadata.usage_restriction must be UsageRestriction.")
        if not isinstance(self.approval_state, ApprovalState):
            raise TypeError("ModelMetadata.approval_state must be ApprovalState.")

    @property
    def is_production_allowed(self) -> bool:
        """是否允許 production invocation。colab_validation_only 永遠不允許。"""
        return self.usage_restriction == UsageRestriction.PRODUCTION


# ─────────────────────────────────────────────────────────────────
# 固定的 CE 與 Formo metadata
# ─────────────────────────────────────────────────────────────────
CE_MODEL_METADATA = ModelMetadata(
    model_id="adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
    revision="v2.0",
    license="other",
    access_status=AccessStatus.OPEN,
    usage_restriction=UsageRestriction.COLAB_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
)

FORMO_MODEL_METADATA = ModelMetadata(
    model_id="formospeech/whisper-large-v3-taiwanese-hakka",
    revision="main",
    license="CC BY-NC 4.0",
    access_status=AccessStatus.GATED,
    usage_restriction=UsageRestriction.COLAB_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
)


# ─────────────────────────────────────────────────────────────────
# Provider Config
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProviderConfig:
    """Provider 設定。"""

    identifier: str
    status: ProviderStatus
    metadata_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ValueError("ProviderConfig.identifier must be non-blank.")
        if not isinstance(self.status, ProviderStatus):
            raise TypeError("ProviderConfig.status must be ProviderStatus.")


# ─────────────────────────────────────────────────────────────────
# Route Config
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RouteConfig:
    """單一語言的路由設定。"""

    route: str
    provider_identifier: str
    enabled: bool

    def __post_init__(self) -> None:
        if not self.route or not self.route.strip():
            raise ValueError("RouteConfig.route must be non-blank.")
        if not self.provider_identifier or not self.provider_identifier.strip():
            raise ValueError("RouteConfig.provider_identifier must be non-blank.")
        if not isinstance(self.enabled, bool):
            raise TypeError("RouteConfig.enabled must be bool.")


# ─────────────────────────────────────────────────────────────────
# AWS Capability Gate — 9 項全部核准才完整
# ─────────────────────────────────────────────────────────────────
_AWS_CAPABILITY_GATE_ITEMS = (
    "region_zh_tw_support",
    "service_input_output_mode",
    "canonical_pcm_compatibility",
    "timeout_behavior",
    "cancellation_behavior",
    "iam_permissions",
    "s3_necessity",
    "s3_result_handling",
    "s3_cleanup_requirement",
)


@dataclass(frozen=True)
class AwsCapabilityGate:
    """
    AWS Capability Gate：9 項核准旗標。

    任何缺項/false/不可讀皆為未核准。全部 True 才是完整。
    """

    region_zh_tw_support: bool
    service_input_output_mode: bool
    canonical_pcm_compatibility: bool
    timeout_behavior: bool
    cancellation_behavior: bool
    iam_permissions: bool
    s3_necessity: bool
    s3_result_handling: bool
    s3_cleanup_requirement: bool
    approval_record_ref: str | None = None

    @property
    def is_complete(self) -> bool:
        """所有 9 項皆為 True 才是完整。"""
        return all(
            getattr(self, item) is True for item in _AWS_CAPABILITY_GATE_ITEMS
        )

    @classmethod
    def default_incomplete(cls) -> "AwsCapabilityGate":
        """建立預設不完整的 gate（所有項目為 False）。"""
        return cls(
            region_zh_tw_support=False,
            service_input_output_mode=False,
            canonical_pcm_compatibility=False,
            timeout_behavior=False,
            cancellation_behavior=False,
            iam_permissions=False,
            s3_necessity=False,
            s3_result_handling=False,
            s3_cleanup_requirement=False,
            approval_record_ref=None,
        )


# ─────────────────────────────────────────────────────────────────
# ASR Config — 頂層受控設定
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AsrConfig:
    """
    後端 ASR 設定。

    包含 language routes、providers、model metadata、AWS capability gate
    與 Formo Prompt ID allowlist。
    """

    routes: dict[str, RouteConfig]
    providers: dict[str, ProviderConfig]
    model_metadata: dict[str, ModelMetadata]
    aws_capability_gate: AwsCapabilityGate
    formo_prompt_id_allowlist: FrozenSet[str]

    def __post_init__(self) -> None:
        # 確認 formo allowlist 精確
        if self.formo_prompt_id_allowlist != _FORMO_PROMPT_ID_ALLOWLIST:
            raise ValueError(
                "AsrConfig.formo_prompt_id_allowlist must contain exactly the "
                "six allowed Formo Prompt IDs."
            )


# ─────────────────────────────────────────────────────────────────
# Config Parser — fail closed
# ─────────────────────────────────────────────────────────────────
class ConfigParseError(Exception):
    """設定解析錯誤 — fail closed。"""

    pass


def _require_key(data: dict[str, Any], key: str, context: str) -> Any:
    """從 dict 取必填欄位，缺失則 fail closed。"""
    if key not in data:
        raise ConfigParseError(
            f"Missing required key '{key}' in {context}. Fail closed."
        )
    return data[key]


def _parse_route_config(data: Any, context: str) -> RouteConfig:
    """解析單一 route config。"""
    if not isinstance(data, dict):
        raise ConfigParseError(f"Route config must be a dict in {context}. Fail closed.")
    route = _require_key(data, "route", context)
    provider_identifier = _require_key(data, "provider_identifier", context)
    enabled = _require_key(data, "enabled", context)
    if not isinstance(enabled, bool):
        raise ConfigParseError(
            f"'enabled' must be bool in {context}. Fail closed."
        )
    return RouteConfig(
        route=route, provider_identifier=provider_identifier, enabled=enabled
    )


def _parse_provider_config(data: Any, context: str) -> ProviderConfig:
    """解析單一 provider config。"""
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"Provider config must be a dict in {context}. Fail closed."
        )
    identifier = _require_key(data, "identifier", context)
    status_str = _require_key(data, "status", context)
    try:
        status = ProviderStatus(status_str)
    except ValueError:
        raise ConfigParseError(
            f"Unknown provider status '{status_str}' in {context}. Fail closed."
        )
    metadata_ref = data.get("metadata_ref")
    return ProviderConfig(
        identifier=identifier, status=status, metadata_ref=metadata_ref
    )


def _parse_model_metadata(data: Any, context: str) -> ModelMetadata:
    """解析單一 model metadata。"""
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"Model metadata must be a dict in {context}. Fail closed."
        )
    model_id = _require_key(data, "model_id", context)
    revision = _require_key(data, "revision", context)
    license_str = _require_key(data, "license", context)
    access_str = _require_key(data, "access_status", context)
    usage_str = _require_key(data, "usage_restriction", context)
    approval_str = _require_key(data, "approval_state", context)

    try:
        access_status = AccessStatus(access_str)
    except ValueError:
        raise ConfigParseError(
            f"Unknown access_status '{access_str}' in {context}. Fail closed."
        )
    try:
        usage_restriction = UsageRestriction(usage_str)
    except ValueError:
        raise ConfigParseError(
            f"Unknown usage_restriction '{usage_str}' in {context}. Fail closed."
        )
    try:
        approval_state = ApprovalState(approval_str)
    except ValueError:
        raise ConfigParseError(
            f"Unknown approval_state '{approval_str}' in {context}. Fail closed."
        )

    return ModelMetadata(
        model_id=model_id,
        revision=revision,
        license=license_str,
        access_status=access_status,
        usage_restriction=usage_restriction,
        approval_state=approval_state,
    )


def _parse_aws_capability_gate(data: Any, context: str) -> AwsCapabilityGate:
    """
    解析 AWS Capability Gate。

    任何缺項、非 bool 值、不可讀皆視為 False（fail closed）。
    """
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"AWS capability gate must be a dict in {context}. Fail closed."
        )

    gate_values: dict[str, bool] = {}
    for item in _AWS_CAPABILITY_GATE_ITEMS:
        value = data.get(item)
        # 非 True 一律視為未核准（fail closed）
        gate_values[item] = value is True

    approval_record_ref = data.get("approval_record_ref")
    if approval_record_ref is not None and not isinstance(approval_record_ref, str):
        raise ConfigParseError(
            f"approval_record_ref must be a string or null in {context}. Fail closed."
        )

    return AwsCapabilityGate(
        **gate_values,
        approval_record_ref=approval_record_ref,
    )


def parse_asr_config(data: Any) -> AsrConfig:
    """
    從 dict-like 資料解析 ASR 設定。

    缺欄位、未知 schema、矛盾狀態或不可讀 approval record 一律 fail closed。

    Args:
        data: dict-like 結構，包含 routes、providers、model_metadata、
              aws_capability_gate 與 formo_prompt_id_allowlist。

    Returns:
        驗證通過的 AsrConfig。

    Raises:
        ConfigParseError: 任何解析錯誤皆 fail closed。
    """
    if not isinstance(data, dict):
        raise ConfigParseError("ASR config must be a dict. Fail closed.")

    # Routes
    routes_data = _require_key(data, "routes", "asr_config")
    if not isinstance(routes_data, dict):
        raise ConfigParseError("'routes' must be a dict in asr_config. Fail closed.")
    routes: dict[str, RouteConfig] = {}
    for lang_key, route_data in routes_data.items():
        routes[lang_key] = _parse_route_config(
            route_data, f"routes[{lang_key}]"
        )

    # Providers
    providers_data = _require_key(data, "providers", "asr_config")
    if not isinstance(providers_data, dict):
        raise ConfigParseError(
            "'providers' must be a dict in asr_config. Fail closed."
        )
    providers: dict[str, ProviderConfig] = {}
    for pid, provider_data in providers_data.items():
        providers[pid] = _parse_provider_config(
            provider_data, f"providers[{pid}]"
        )

    # Model metadata
    metadata_data = _require_key(data, "model_metadata", "asr_config")
    if not isinstance(metadata_data, dict):
        raise ConfigParseError(
            "'model_metadata' must be a dict in asr_config. Fail closed."
        )
    model_metadata: dict[str, ModelMetadata] = {}
    for mid, meta_data in metadata_data.items():
        model_metadata[mid] = _parse_model_metadata(
            meta_data, f"model_metadata[{mid}]"
        )

    # AWS capability gate
    gate_data = _require_key(data, "aws_capability_gate", "asr_config")
    aws_capability_gate = _parse_aws_capability_gate(
        gate_data, "aws_capability_gate"
    )

    # Formo Prompt ID allowlist
    formo_data = _require_key(data, "formo_prompt_id_allowlist", "asr_config")
    if not isinstance(formo_data, (list, set, frozenset)):
        raise ConfigParseError(
            "'formo_prompt_id_allowlist' must be a list/set in asr_config. Fail closed."
        )
    formo_allowlist = frozenset(formo_data)

    # 驗證 allowlist 精確
    if formo_allowlist != _FORMO_PROMPT_ID_ALLOWLIST:
        raise ConfigParseError(
            "formo_prompt_id_allowlist must contain exactly the six allowed values. "
            "Fail closed."
        )

    # 驗證矛盾狀態：CE/Formo metadata 不可為 production
    for mid, meta in model_metadata.items():
        if meta.model_id in (
            CE_MODEL_METADATA.model_id,
            FORMO_MODEL_METADATA.model_id,
        ) and meta.usage_restriction != UsageRestriction.COLAB_VALIDATION_ONLY:
            raise ConfigParseError(
                f"Model '{meta.model_id}' must have usage_restriction "
                f"'colab_validation_only'. Contradictory state. Fail closed."
            )

    return AsrConfig(
        routes=routes,
        providers=providers,
        model_metadata=model_metadata,
        aws_capability_gate=aws_capability_gate,
        formo_prompt_id_allowlist=formo_allowlist,
    )


# ─────────────────────────────────────────────────────────────────
# Convenience: 建立拒絕結果
# ─────────────────────────────────────────────────────────────────
def make_route_not_approved_error(reason: str) -> TypedAsrError:
    """建立 route_not_approved 錯誤。"""
    return TypedAsrError(
        category=AsrErrorCategory.ROUTE_NOT_APPROVED,
        message=reason,
        retryable=False,
    )


def make_unsupported_language_error(language: str) -> TypedAsrError:
    """建立 unsupported_language 錯誤。"""
    return TypedAsrError(
        category=AsrErrorCategory.UNSUPPORTED_LANGUAGE,
        message=f"Unsupported language: {language!r}.",
        retryable=False,
    )
