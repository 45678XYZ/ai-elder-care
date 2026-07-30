"""
ASR 領域套件 — 公開表面。

位於 backend/src/shared/asr/，公開領域型別、設定與輔助功能。
消費者可以 `from src.shared.asr import ...` 存取所有公開型別。

禁止依賴：handlers、HTTP、DB。
"""
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)
from .canonical_audio import canonicalize
from .config import (
    AccessStatus,
    ApprovalState,
    AsrConfig,
    AwsCapabilityGate,
    CE_MODEL_METADATA,
    COLAB_CANDIDATE_MODEL_IDS,
    ConcurrencyPolicy,
    ConfigParseError,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    ModelProductionGate,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    UsageRestriction,
    make_provider_failure_error,
    make_provider_invalid_response_error,
    make_provider_unavailable_error,
    make_route_not_approved_error,
    make_unsupported_language_error,
    parse_asr_config,
    validate_formo_prompt_id,
)
from .concurrency import (
    LazyModelHandle,
    ModelLoadUnavailable,
    ModelSlotPool,
    SlotLease,
    SlotPoolStats,
)
from .providers import (
    AsrProvider,
    AttemptRecord,
    ConcurrentAsrProvider,
    TransportRequest,
)
from .failover import (
    DEFAULT_FAILOVER_CATEGORIES,
    NON_FAILOVER_CATEGORIES,
    ChainOutcome,
    FailoverChain,
)
from .local_models import CeLocalProvider, FormoLocalProvider, LocalModelSpec
from .composition import (
    MODEL_PROVIDER_REGISTRY,
    ModelProviderRegistration,
    StdoutTelemetrySink,
    build_facade,
    build_provider_registry,
    default_config,
    get_asr_facade,
    load_config,
    reset_asr_facade,
)
from .hak_mock import HakMockProvider
from .aws_zh_adapter import (
    AwsZhAdapter,
    FakeTransport,
    TransportCancelled,
    TransportDeadlineExceeded,
    TransportUnavailable,
)
from .router import AsrRouter
from .facade import AsrFacade
from .telemetry import (
    DeadlineOutcome,
    SafeTelemetryRecord,
    TelemetrySink,
    TerminalOutcome,
    TerminalTelemetryEmitter,
    TELEMETRY_ALLOWLIST_KEYS,
)
from .evidence import (
    ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS,
    ADR_MANDATORY_HEADINGS,
    EVIDENCE_REDACTED_FIELDS,
    EVIDENCE_REQUIRED_FIELDS,
    EvidenceValidationError,
    validate_adr_evidence_reference,
    validate_adr_template_headings,
    validate_evidence_record,
)

__all__ = [
    # canonical_audio.py
    "canonicalize",
    # types.py
    "InputFormat",
    "Language",
    "AsrErrorCategory",
    "TypedAsrError",
    "Transcript",
    "AsrTerminalResult",
    "CorrelationContext",
    "CanonicalAudio",
    "Deadline",
    "CancellationSignal",
    # config.py
    "UsageRestriction",
    "AccessStatus",
    "ApprovalState",
    "ProviderStatus",
    "ModelMetadata",
    "CE_MODEL_METADATA",
    "FORMO_MODEL_METADATA",
    "ModelProductionGate",
    "COLAB_CANDIDATE_MODEL_IDS",
    "ProviderConfig",
    "ProviderKind",
    "RouteConfig",
    "AwsCapabilityGate",
    "ConcurrencyPolicy",
    "AsrConfig",
    "ConfigParseError",
    "parse_asr_config",
    "validate_formo_prompt_id",
    "make_route_not_approved_error",
    "make_unsupported_language_error",
    "make_provider_unavailable_error",
    "make_provider_failure_error",
    "make_provider_invalid_response_error",
    # concurrency.py
    "ModelSlotPool",
    "SlotLease",
    "SlotPoolStats",
    "LazyModelHandle",
    "ModelLoadUnavailable",
    # providers.py
    "AsrProvider",
    "AttemptRecord",
    "ConcurrentAsrProvider",
    "TransportRequest",
    # failover.py
    "FailoverChain",
    "ChainOutcome",
    "DEFAULT_FAILOVER_CATEGORIES",
    "NON_FAILOVER_CATEGORIES",
    # local_models.py
    "LocalModelSpec",
    "CeLocalProvider",
    "FormoLocalProvider",
    # composition.py
    "default_config",
    "load_config",
    "build_provider_registry",
    "build_facade",
    "get_asr_facade",
    "reset_asr_facade",
    "StdoutTelemetrySink",
    "MODEL_PROVIDER_REGISTRY",
    "ModelProviderRegistration",
    # hak_mock.py
    "HakMockProvider",
    # aws_zh_adapter.py
    "AwsZhAdapter",
    "FakeTransport",
    "TransportCancelled",
    "TransportDeadlineExceeded",
    "TransportUnavailable",
    # router.py
    "AsrRouter",
    # facade.py
    "AsrFacade",
    # telemetry.py
    "DeadlineOutcome",
    "SafeTelemetryRecord",
    "TelemetrySink",
    "TerminalOutcome",
    "TerminalTelemetryEmitter",
    "TELEMETRY_ALLOWLIST_KEYS",
    # evidence.py
    "ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS",
    "ADR_MANDATORY_HEADINGS",
    "EVIDENCE_REDACTED_FIELDS",
    "EVIDENCE_REQUIRED_FIELDS",
    "EvidenceValidationError",
    "validate_adr_evidence_reference",
    "validate_adr_template_headings",
    "validate_evidence_record",
]
