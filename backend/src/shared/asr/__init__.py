"""Remote-only ASR 領域套件的公開介面。"""

from .canonical_audio import canonicalize
from .composition import (
    REMOTE_MODEL_LANGUAGES,
    StdoutTelemetrySink,
    build_facade,
    build_provider_registry,
    default_config,
    get_asr_facade,
    load_config,
    reset_asr_facade,
)
from .config import (
    AccessStatus,
    ApprovalState,
    AsrConfig,
    CE_MODEL_METADATA,
    COLAB_CANDIDATE_MODEL_IDS,
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
from .facade import AsrFacade
from .providers import (
    AsrProvider,
    HakMockProvider,
    RemoteEndpointSpec,
    SageMakerAsrProvider,
)
from .router import AsrRouter, RouteOutcome
from .telemetry import (
    TELEMETRY_ALLOWLIST_KEYS,
    DeadlineOutcome,
    SafeTelemetryRecord,
    TelemetrySink,
    TerminalOutcome,
    TerminalTelemetryEmitter,
)
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    HakkaDialect,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

__all__ = [name for name in globals() if not name.startswith("_")]
