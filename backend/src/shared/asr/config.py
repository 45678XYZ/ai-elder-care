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
# Provider 種類 — 決定「這個 provider 需要通過哪一道核准閘門」
# ─────────────────────────────────────────────────────────────────
class ProviderKind(enum.Enum):
    """
    Provider 種類。

    router 靠它決定要驗哪一道閘門，而不是靠 provider 名稱猜：

    - `mock`：不呼叫模型、網路或雲端服務，只需 status 為 enabled。
    - `local_model`：在本 process 執行推論，必須有 model metadata 且
      production gate 逐項核准。
    - `remote_model`：呼叫我們自己託管的推論端點（SageMaker）。閘門與
      `local_model` 相同——是同一個模型，只是換個地方跑——另外必須有
      endpoint_name。
    - `aws_managed`：呼叫 AWS 代管的現成 ASR 服務，必須 AWS capability gate
      九項全核准。與 `remote_model` 的差別是「服務是誰的模型」。
    """

    MOCK = "mock"
    LOCAL_MODEL = "local_model"
    REMOTE_MODEL = "remote_model"
    AWS_MANAGED = "aws_managed"

    @property
    def requires_model_approval(self) -> bool:
        """是否受 model production gate 管制。"""
        return self in (ProviderKind.LOCAL_MODEL, ProviderKind.REMOTE_MODEL)


# ─────────────────────────────────────────────────────────────────
# Model Production Gate — 逐項人工核准才允許 production invocation
# ─────────────────────────────────────────────────────────────────
_MODEL_PRODUCTION_GATE_ITEMS = (
    "colab_validation_passed",
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

    - `colab_validation_passed`：模型已在 Colab 人工驗證流程跑出可用結果。
    - `license_cleared`：授權允許本專案的實際用途。Formo 為 CC BY-NC 4.0
      （限非商業），一旦本專案轉為商業用途就不得核准這一項。
    - `access_granted`：gated model 的存取權限已取得。
    - `quota_cleared`：推論用量／額度已確認足夠。
    - `runtime_capacity_verified`：執行環境（GPU 記憶體、併發數）已實測可承載。
    """

    colab_validation_passed: bool = False
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
    usage_restriction=UsageRestriction.COLAB_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
    production_gate=ModelProductionGate.default_incomplete(),
)

# CC BY-NC 4.0 限非商業用途：若本專案轉為商業服務，license_cleared 不得核准。
FORMO_MODEL_METADATA = ModelMetadata(
    model_id="formospeech/whisper-large-v3-taiwanese-hakka",
    revision="main",
    license="CC BY-NC 4.0",
    access_status=AccessStatus.GATED,
    usage_restriction=UsageRestriction.COLAB_VALIDATION_ONLY,
    approval_state=ApprovalState.NOT_APPROVED,
    production_gate=ModelProductionGate.default_incomplete(),
)

# CE/Formo 的 model id 集合——router 與 parser 用它辨識「需要 production gate
# 才可上線的候選模型」。
COLAB_CANDIDATE_MODEL_IDS: FrozenSet[str] = frozenset(
    {CE_MODEL_METADATA.model_id, FORMO_MODEL_METADATA.model_id}
)


# ─────────────────────────────────────────────────────────────────
# Provider Config
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProviderConfig:
    """
    Provider 設定。

    `max_concurrent` 是這個 provider 允許的同時推論數。實體模型 handle 不可重入，
    預設 1 代表序列化執行；提高這個值前必須確認執行環境真的能同時容納。
    """

    identifier: str
    status: ProviderStatus
    metadata_ref: str | None = None
    max_concurrent: int = 1
    kind: ProviderKind = ProviderKind.MOCK
    endpoint_name: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ValueError("ProviderConfig.identifier must be non-blank.")
        if not isinstance(self.status, ProviderStatus):
            raise TypeError("ProviderConfig.status must be ProviderStatus.")
        if not isinstance(self.max_concurrent, int) or self.max_concurrent < 1:
            raise ValueError("ProviderConfig.max_concurrent must be an integer >= 1.")
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
    provider。備援只在主 provider 判定為「provider 自身有問題」或「已飽和」時才
    啟用，語言、音訊、核准類錯誤不會觸發備援。
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
# Concurrency Policy — 併發等待與溢流上限
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ConcurrencyPolicy:
    """
    併發等待政策。

    `spill_wait_ms` 是主 provider 飽和時願意排隊的時間；超過就溢流到備援
    provider，而不是無界排隊把呼叫端的 deadline 吃光。
    `model_load_wait_ms` 是等待其他請求完成模型載入的上限。
    所有等待都會再被呼叫端 deadline 的剩餘時間夾住，取兩者較小值。
    """

    spill_wait_ms: int = 250
    model_load_wait_ms: int = 15_000
    load_retry_cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.spill_wait_ms < 0:
            raise ValueError("ConcurrencyPolicy.spill_wait_ms must be non-negative.")
        if self.model_load_wait_ms < 0:
            raise ValueError(
                "ConcurrencyPolicy.model_load_wait_ms must be non-negative."
            )
        if self.load_retry_cooldown_seconds < 0:
            raise ValueError(
                "ConcurrencyPolicy.load_retry_cooldown_seconds must be non-negative."
            )

    @property
    def spill_wait_seconds(self) -> float:
        return self.spill_wait_ms / 1000.0

    @property
    def model_load_wait_seconds(self) -> float:
        return self.model_load_wait_ms / 1000.0


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

    包含 language routes、providers、model metadata、AWS capability gate、
    併發政策與 Formo Prompt ID allowlist。
    """

    routes: dict[str, RouteConfig]
    providers: dict[str, ProviderConfig]
    model_metadata: dict[str, ModelMetadata]
    aws_capability_gate: AwsCapabilityGate
    formo_prompt_id_allowlist: FrozenSet[str]
    concurrency: ConcurrencyPolicy = ConcurrencyPolicy()

    def __post_init__(self) -> None:
        # 確認 formo allowlist 精確
        if self.formo_prompt_id_allowlist != _FORMO_PROMPT_ID_ALLOWLIST:
            raise ValueError(
                "AsrConfig.formo_prompt_id_allowlist must contain exactly the "
                "six allowed Formo Prompt IDs."
            )
        if not isinstance(self.concurrency, ConcurrencyPolicy):
            raise TypeError("AsrConfig.concurrency must be ConcurrencyPolicy.")

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

    # max_concurrent 為選填；非正整數一律 fail closed，不靜默降為 1。
    raw_max_concurrent = data.get("max_concurrent", 1)
    if isinstance(raw_max_concurrent, bool) or not isinstance(raw_max_concurrent, int):
        raise ConfigParseError(
            f"'max_concurrent' must be an integer in {context}. Fail closed."
        )

    # kind 為選填；未知值一律 fail closed，不預設放行成 mock。
    raw_kind = data.get("kind", ProviderKind.MOCK.value)
    try:
        kind = ProviderKind(raw_kind)
    except ValueError:
        raise ConfigParseError(
            f"Unknown provider kind '{raw_kind}' in {context}. Fail closed."
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
            max_concurrent=raw_max_concurrent,
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

    # 併發政策（選填；缺區塊用預設值）
    concurrency = _parse_concurrency_policy(
        data.get("concurrency"), "concurrency"
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
        aws_capability_gate=aws_capability_gate,
        formo_prompt_id_allowlist=formo_allowlist,
        concurrency=concurrency,
    )


def _parse_concurrency_policy(data: Any, context: str) -> ConcurrencyPolicy:
    """解析併發政策；缺區塊用預設值，型別或數值不合一律 fail closed。"""
    if data is None:
        return ConcurrencyPolicy()
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"Concurrency policy must be a dict or null in {context}. Fail closed."
        )

    defaults = ConcurrencyPolicy()
    spill_wait_ms = data.get("spill_wait_ms", defaults.spill_wait_ms)
    model_load_wait_ms = data.get("model_load_wait_ms", defaults.model_load_wait_ms)
    cooldown = data.get(
        "load_retry_cooldown_seconds", defaults.load_retry_cooldown_seconds
    )

    for name, value in (
        ("spill_wait_ms", spill_wait_ms),
        ("model_load_wait_ms", model_load_wait_ms),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigParseError(
                f"'{name}' must be an integer in {context}. Fail closed."
            )
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)):
        raise ConfigParseError(
            f"'load_retry_cooldown_seconds' must be a number in {context}. Fail closed."
        )

    try:
        return ConcurrencyPolicy(
            spill_wait_ms=spill_wait_ms,
            model_load_wait_ms=model_load_wait_ms,
            load_retry_cooldown_seconds=float(cooldown),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigParseError(
            f"Invalid concurrency policy in {context}: {exc} Fail closed."
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
