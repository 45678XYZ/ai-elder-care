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
    ConfigParseError,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    ProviderConfig,
    ProviderStatus,
    RouteConfig,
    UsageRestriction,
    make_route_not_approved_error,
    make_unsupported_language_error,
    parse_asr_config,
    validate_formo_prompt_id,
)
from .providers import AsrProvider, TransportRequest
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
    "ProviderConfig",
    "RouteConfig",
    "AwsCapabilityGate",
    "AsrConfig",
    "ConfigParseError",
    "parse_asr_config",
    "validate_formo_prompt_id",
    "make_route_not_approved_error",
    "make_unsupported_language_error",
    # providers.py
    "AsrProvider",
    "TransportRequest",
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
