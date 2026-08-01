"""
ASR 後端受控設定：route、provider 與 model production gate。

解析失敗、缺必填鍵或相互矛盾的狀態一律 fail closed。

禁止依賴：handlers、HTTP、DB、AWS SDK、環境自動選服務/Region。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .types import AsrErrorCategory, TypedAsrError


# ─────────────────────────────────────────────────────────────────
# 用途限制
# ─────────────────────────────────────────────────────────────────
class UsageRestriction(enum.Enum):
    """模型用途限制。"""

    STAGING_VALIDATION_ONLY = "staging_validation_only"
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
    STAGING_ONLY = "staging_only"


# ─────────────────────────────────────────────────────────────────
# Provider 種類 — 決定「這個 provider 需要通過哪一道核准閘門」
# ─────────────────────────────────────────────────────────────────
class ProviderKind(enum.Enum):
    """
    Provider 種類。

    router 靠它決定要驗哪一道閘門，而不是靠 provider 名稱猜：

    - `mock`：不呼叫模型、網路或雲端服務，只需 status 為 enabled。
    - `remote_model`：呼叫我們自己託管的推論端點（SageMaker）。必須有
      model metadata 且 production gate 逐項核准，另外必須有 endpoint_name。

    - `aws_managed`：呼叫受控的 AWS 管理式 ASR；不綁定模型 metadata，
      實際服務與語言能力由 composition allowlist 決定。

    已移除（remote-only 架構）：
    - `local_model`：Lambda 不可在 process 內執行模型推論。
    """

    MOCK = "mock"
    REMOTE_MODEL = "remote_model"
    AWS_MANAGED = "aws_managed"

    @property
    def requires_model_approval(self) -> bool:
        """是否受 model production gate 管制。"""
        return self is ProviderKind.REMOTE_MODEL


# ─────────────────────────────────────────────────────────────────
# Model Production Gate — 逐項人工核准才允許 production invocation
# ─────────────────────────────────────────────────────────────────
_MODEL_PRODUCTION_GATE_ITEMS = (
    "staging_validation_passed",
    "license_cleared",
    "access_granted",
    "quota_cleared",
    "runtime_capacity_verified",
)


@dataclass(frozen=True)
class ModelProductionGate:
    """
    單一模型的 production 核准閘門。

    每一項都是必須由人確認、無法由程式推導的外部事實，因此全部預設 False：

    - `staging_validation_passed`：模型已在目標 SageMaker instance 的 staging
      環境跑出可用結果。
    - `license_cleared`：授權允許本專案的實際用途。Formo 為 CC BY-NC 4.0
      （限非商業），一旦本專案轉為商業用途就不得核准這一項。
    - `access_granted`：gated model 的存取權限已取得。
    - `quota_cleared`：推論用量／額度已確認足夠。
    - `runtime_capacity_verified`：執行環境（GPU 記憶體、併發數）已實測可承載。
    """

    staging_validation_passed: bool = False
    license_cleared: bool = False
    access_granted: bool = False
    quota_cleared: bool = False
    runtime_capacity_verified: bool = False
    approval_record_ref: str | None = None

    @property
    def is_approved(self) -> bool:
        """所有項目皆為 True 才算核准。任一缺項/False 即未核准。"""
        return all(
            getattr(self, item) is True for item in _MODEL_PRODUCTION_GATE_ITEMS
        )

    @property
    def missing_items(self) -> tuple[str, ...]:
        """尚未核准的項目名稱，供安全錯誤訊息使用（不含任何敏感內容）。"""
        return tuple(
            item
            for item in _MODEL_PRODUCTION_GATE_ITEMS
            if getattr(self, item) is not True
        )

    @classmethod
    def default_incomplete(cls) -> "ModelProductionGate":
        """建立預設不完整的 gate（所有項目為 False）。"""
        return cls()


# ─────────────────────────────────────────────────────────────────
# Model Metadata — 不可變
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelMetadata:
    """
    模型中繼資料。

    保存 model ID、version/revision、license、access status、usage restriction、
    approval state 與 production gate。
    """

    model_id: str
    revision: str
    license: str
    access_status: AccessStatus
    usage_restriction: UsageRestriction
    approval_state: ApprovalState
    production_gate: ModelProductionGate = ModelProductionGate()

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
        if not isinstance(self.production_gate, ModelProductionGate):
            raise TypeError(
                "ModelMetadata.production_gate must be ModelProductionGate."
            )

    @property
    def is_production_allowed(self) -> bool:
        """
        是否允許 production invocation。

        三個條件必須同時成立：用途標為 production、approval_state 為 approved、
        且 production gate 逐項核准。任一不成立即不允許——這是 fail-closed 的
        單一判斷點，router 與 provider 都以它為準。
        """
        return (
            self.usage_restriction == UsageRestriction.PRODUCTION
            and self.approval_state == ApprovalState.APPROVED
            and self.production_gate.is_approved
        )


# ─────────────────────────────────────────────────────────────────
# 固定的 CE 與 Formo metadata
# ─────────────────────────────────────────────────────────────────
# 這兩筆是「模型事實」的預設值，不是可用 provider。兩者的 production gate 皆為
# 預設不完整，因此在任何人手動核准之前，production route 一律 fail closed。
CE_MODEL_METADATA = ModelMetadata(
    model_id="adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
    revision="v2.0",
    license="other",
    access_status=AccessStatus.OPEN,
    usage_restriction=UsageRestriction.STAGING_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
    production_gate=ModelProductionGate.default_incomplete(),
)

# CC BY-NC 4.0 限非商業用途：若本專案轉為商業服務，license_cleared 不得核准。
FORMO_MODEL_METADATA = ModelMetadata(
    model_id="formospeech/whisper-large-v3-taiwanese-hakka",
    revision="main",
    license="CC BY-NC 4.0",
    access_status=AccessStatus.GATED,
    usage_restriction=UsageRestriction.STAGING_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
    # 使用者已取得 gated repository 讀取權；其餘 production gate 仍需 staging
    # 與目標 instance 的 runtime 證據，故模型仍維持 fail closed。
    production_gate=ModelProductionGate(access_granted=True),
)

# CE/Formo 的 model id 集合——router 與 parser 用它辨識「需要 production gate
# 才可上線的候選模型」。
STAGING_CANDIDATE_MODEL_IDS: frozenset[str] = frozenset(
    {CE_MODEL_METADATA.model_id, FORMO_MODEL_METADATA.model_id}
)


# ─────────────────────────────────────────────────────────────────
# Provider Config
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProviderConfig:
    """
    Provider 設定。

    遠端 provider 只保存 endpoint 與模型 metadata reference；容量由 SageMaker 管理。
    """

    identifier: str
    status: ProviderStatus
    metadata_ref: str | None = None
    kind: ProviderKind = ProviderKind.MOCK
    endpoint_name: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ValueError("ProviderConfig.identifier must be non-blank.")
        if not isinstance(self.status, ProviderStatus):
            raise TypeError("ProviderConfig.status must be ProviderStatus.")
        if not isinstance(self.kind, ProviderKind):
            raise TypeError("ProviderConfig.kind must be ProviderKind.")
        if self.endpoint_name is not None and not self.endpoint_name.strip():
            raise ValueError(
                "ProviderConfig.endpoint_name must be non-blank when provided."
            )


# ─────────────────────────────────────────────────────────────────
# Route Config
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RouteConfig:
    """
    單一語言的路由設定。

    `provider_identifier` 是主 provider；`fallback_chain` 是它之後依序嘗試的備援
    provider。備援只在主 provider 發生可重試的遠端錯誤時啟用；語言、音訊、
    核准類錯誤不會觸發備援。
    """

    route: str
    provider_identifier: str
    enabled: bool
    fallback_chain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.route or not self.route.strip():
            raise ValueError("RouteConfig.route must be non-blank.")
        if not self.provider_identifier or not self.provider_identifier.strip():
            raise ValueError("RouteConfig.provider_identifier must be non-blank.")
        if not isinstance(self.enabled, bool):
            raise TypeError("RouteConfig.enabled must be bool.")
        if not isinstance(self.fallback_chain, tuple):
            raise TypeError("RouteConfig.fallback_chain must be a tuple.")
        for entry in self.fallback_chain:
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError(
                    "RouteConfig.fallback_chain entries must be non-blank strings."
                )
        if self.provider_identifier in self.fallback_chain:
            raise ValueError(
                "RouteConfig.fallback_chain must not repeat the primary provider."
            )
        if len(set(self.fallback_chain)) != len(self.fallback_chain):
            raise ValueError(
                "RouteConfig.fallback_chain must not contain duplicate providers."
            )

    @property
    def provider_order(self) -> tuple[str, ...]:
        """主 provider 在前的完整嘗試順序。"""
        return (self.provider_identifier, *self.fallback_chain)


# ─────────────────────────────────────────────────────────────────
# ASR Config — 頂層受控設定
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AsrConfig:
    """
    後端 ASR 設定（remote-only 架構）。

    包含 language routes、providers 與 model metadata。

    ASR_CONFIG_JSON 是 Lambda 唯一的 ASR 設定來源。
    """

    routes: dict[str, RouteConfig]
    providers: dict[str, ProviderConfig]
    model_metadata: dict[str, ModelMetadata]

    def metadata_for_provider(self, provider_id: str) -> ModelMetadata | None:
        """取得 provider 綁定的 model metadata；未綁定或查不到回 None。"""
        provider = self.providers.get(provider_id)
        if provider is None or not provider.metadata_ref:
            return None
        return self.model_metadata.get(provider.metadata_ref)


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

    # fallback_chain 為選填；型別或內容不合一律 fail closed，不做猜測補值。
    raw_chain = data.get("fallback_chain", ())
    if isinstance(raw_chain, (list, tuple)):
        fallback_chain = tuple(raw_chain)
    else:
        raise ConfigParseError(
            f"'fallback_chain' must be a list in {context}. Fail closed."
        )

    try:
        return RouteConfig(
            route=route,
            provider_identifier=provider_identifier,
            enabled=enabled,
            fallback_chain=fallback_chain,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigParseError(f"Invalid route config in {context}: {exc} Fail closed.")


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

    # kind 為選填；未知值一律 fail closed，不預設放行成 mock。
    raw_kind = data.get("kind", ProviderKind.MOCK.value)
    try:
        kind = ProviderKind(raw_kind)
    except ValueError:
        raise ConfigParseError(
            f"Unknown provider kind '{raw_kind}' in {context}. Fail closed."
        )

    if kind is ProviderKind.AWS_MANAGED:
        allowed_keys = {"identifier", "status", "kind"}
        extra_keys = set(data) - allowed_keys
        if extra_keys:
            raise ConfigParseError(
                f"AWS managed provider contains unsupported keys in {context}. "
                "Fail closed."
            )

    endpoint_name = data.get("endpoint_name")
    if endpoint_name is not None and not isinstance(endpoint_name, str):
        raise ConfigParseError(
            f"'endpoint_name' must be a string or null in {context}. Fail closed."
        )

    try:
        return ProviderConfig(
            identifier=identifier,
            status=status,
            metadata_ref=metadata_ref,
            kind=kind,
            endpoint_name=endpoint_name,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigParseError(
            f"Invalid provider config in {context}: {exc} Fail closed."
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

    production_gate = _parse_model_production_gate(
        data.get("production_gate"), f"{context}.production_gate"
    )

    return ModelMetadata(
        model_id=model_id,
        revision=revision,
        license=license_str,
        access_status=access_status,
        usage_restriction=usage_restriction,
        approval_state=approval_state,
        production_gate=production_gate,
    )


def _parse_model_production_gate(data: Any, context: str) -> ModelProductionGate:
    """
    解析模型 production gate。

    缺區塊、缺項、非 bool 值一律視為未核准（fail closed）；不猜測、不預設放行。
    """
    if data is None:
        return ModelProductionGate.default_incomplete()
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"Model production gate must be a dict or null in {context}. Fail closed."
        )

    gate_values = {
        item: data.get(item) is True for item in _MODEL_PRODUCTION_GATE_ITEMS
    }

    approval_record_ref = data.get("approval_record_ref")
    if approval_record_ref is not None and not isinstance(approval_record_ref, str):
        raise ConfigParseError(
            f"approval_record_ref must be a string or null in {context}. Fail closed."
        )

    return ModelProductionGate(
        **gate_values, approval_record_ref=approval_record_ref
    )


def parse_asr_config(data: Any) -> AsrConfig:
    """
    從 dict-like 資料解析 ASR 設定（remote-only 架構）。

    缺欄位、矛盾狀態或不可讀 approval record 一律 fail closed。
    ASR_CONFIG_JSON 是 Lambda 唯一的 ASR 設定來源。

    Args:
        data: dict-like 結構，包含 routes、providers 與 model_metadata。

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

    # 驗證矛盾狀態：宣告 production 的模型必須同時具備 approved 的 approval_state
    # 與逐項核准的 production gate。少任何一半就是自相矛盾的設定，fail closed，
    # 避免「標成 production 但其實沒人核准」的組合悄悄上線。
    for mid, meta in model_metadata.items():
        if meta.usage_restriction != UsageRestriction.PRODUCTION:
            continue
        if meta.approval_state != ApprovalState.APPROVED:
            raise ConfigParseError(
                f"Model '{meta.model_id}' in model_metadata[{mid}] declares "
                f"usage_restriction 'production' but approval_state is "
                f"'{meta.approval_state.value}'. Contradictory state. Fail closed."
            )
        if not meta.production_gate.is_approved:
            raise ConfigParseError(
                f"Model '{meta.model_id}' in model_metadata[{mid}] declares "
                f"usage_restriction 'production' but its production gate is "
                f"incomplete: missing {list(meta.production_gate.missing_items)}. "
                f"Fail closed."
            )

    # 驗證參照完整性：route 的主 provider 與所有備援 provider 都必須存在。
    # 指向不存在的 provider 會讓備援鏈在執行期才爆，所以在解析階段就攔下來。
    for lang_key, route in routes.items():
        for provider_id in route.provider_order:
            if provider_id not in providers:
                raise ConfigParseError(
                    f"routes[{lang_key}] references unknown provider "
                    f"'{provider_id}'. Fail closed."
                )

    return AsrConfig(
        routes=routes,
        providers=providers,
        model_metadata=model_metadata,
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


def make_provider_unavailable_error(reason: str) -> TypedAsrError:
    """建立 provider_unavailable 錯誤。reason 必須是安全訊息，不含例外文字。"""
    return TypedAsrError(
        category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
        message=reason,
        retryable=True,
    )


def make_provider_failure_error(reason: str) -> TypedAsrError:
    """建立 provider_failure 錯誤。reason 必須是安全訊息，不含例外文字。"""
    return TypedAsrError(
        category=AsrErrorCategory.PROVIDER_FAILURE,
        message=reason,
        retryable=True,
    )


def make_provider_invalid_response_error(reason: str) -> TypedAsrError:
    """建立 provider_invalid_response 錯誤。reason 不得含 provider 原始回應。"""
    return TypedAsrError(
        category=AsrErrorCategory.PROVIDER_INVALID_RESPONSE,
        message=reason,
        retryable=False,
    )
