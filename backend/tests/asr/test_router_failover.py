"""
Router 與備援鏈整合測試 — provider registry、核准資格與執行期轉移。

覆蓋 fail-closed 之外的另一半：**核准之後真的能用**，以及主力壞掉時
router 真的會改用備援。
"""
from __future__ import annotations

import time

import pytest

from src.shared.asr.config import (
    AccessStatus,
    ApprovalState,
    AsrConfig,
    CE_MODEL_METADATA,
    ModelMetadata,
    ModelProductionGate,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    UsageRestriction,
)
from src.shared.asr.providers import AMAZON_TRANSCRIBE_PROVIDER_ID
from src.shared.asr.router import AsrRouter
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

FORMO_ALLOWLIST = frozenset(
    {
        "htia_sixian",
        "htia_hailu",
        "htia_dapu",
        "htia_raoping",
        "htia_zhaoan",
        "htia_nansixian",
    }
)

APPROVED_GATE = ModelProductionGate(
    staging_validation_passed=True,
    license_cleared=True,
    access_granted=True,
    quota_cleared=True,
    runtime_capacity_verified=True,
    approval_record_ref="docs/adr/asr-ce-production-approval.md",
)

APPROVED_CE_METADATA = ModelMetadata(
    model_id=CE_MODEL_METADATA.model_id,
    revision=CE_MODEL_METADATA.revision,
    license=CE_MODEL_METADATA.license,
    access_status=AccessStatus.OPEN,
    usage_restriction=UsageRestriction.PRODUCTION,
    approval_state=ApprovalState.APPROVED,
    production_gate=APPROVED_GATE,
)


def make_audio() -> CanonicalAudio:
    return CanonicalAudio(
        pcm_s16le=b"\x00\x00" * 160,
        sample_rate_hz=16_000,
        channels=1,
        sample_width_bits=16,
        duration_ms=10,
        input_format=InputFormat.WAV,
    )


def deadline_in(seconds: float) -> Deadline:
    return Deadline.after(seconds, time.monotonic)


class SpyProvider:
    """可設定結果的假 provider，記錄被呼叫次數。"""

    def __init__(self, provider_id: str, result) -> None:
        self.provider_id = provider_id
        self._result = result
        self.calls = 0

    def transcribe(self, audio, language, deadline, cancellation, context):
        self.calls += 1
        return self._result


def build_config(
    *,
    primary: str,
    fallback: tuple[str, ...] = (),
    providers: dict[str, ProviderConfig],
    metadata: dict[str, ModelMetadata] | None = None,
    language: str = "hak",
   ) -> AsrConfig:
    return AsrConfig(
        routes={
            language: RouteConfig(
                route=f"{language}_route",
                provider_identifier=primary,
                enabled=True,
                fallback_chain=fallback,
            )
        },
        providers=providers,
        model_metadata=metadata or {},
    )


def route(router: AsrRouter, language: Language = Language.HAK):
    return router.route_detailed(
        make_audio(),
        language,
        deadline_in(5.0),
        CancellationSignal(),
        CorrelationContext(correlation_id="corr-router-failover"),
    )


# ─────────────────────────────────────────────────────────────────
# 核准後可上線
# ─────────────────────────────────────────────────────────────────
def test_approved_local_model_provider_is_used() -> None:
    """production gate 逐項核准後，實體模型 provider 必須真的被選用（remote）。"""
    spy = SpyProvider("ce_remote", Transcript(text="核准後的辨識結果"))
    config = build_config(
        primary="ce_remote",
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            )
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(config, providers={"ce_remote": spy})

    outcome = route(router)

    assert isinstance(outcome.result, Transcript)
    assert outcome.result.text == "核准後的辨識結果"
    assert outcome.served_provider_id == "ce_remote"
    assert spy.calls == 1


def test_unapproved_local_model_provider_is_not_used() -> None:
    """未核准的模型即使有 provider 實例也不得被呼叫。"""
    spy = SpyProvider("ce_remote", Transcript(text="不該被用到"))
    config = build_config(
        primary="ce_remote",
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            )
        },
        metadata={"ce": CE_MODEL_METADATA},  # 預設 gate 全 False
    )
    router = AsrRouter(config, providers={"ce_remote": spy})

    outcome = route(router)

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert spy.calls == 0
    assert outcome.attempt_count == 0


# ─────────────────────────────────────────────────────────────────
# 資格篩選
# ─────────────────────────────────────────────────────────────────
def test_unapproved_formo_primary_does_not_fall_through_to_ce() -> None:
    """主力未核准是 route gate 失敗，不得繞過它直接呼叫 CE。"""
    unapproved = SpyProvider("formo_remote", Transcript(text="不該被用到"))
    approved = SpyProvider("ce_remote", Transcript(text="備援結果"))
    config = build_config(
        primary="formo_remote",
        fallback=("ce_remote",),
        providers={
            "formo_remote": ProviderConfig(
                identifier="formo_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="formo",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-formo",
            ),
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
        },
        metadata={
            "formo": CE_MODEL_METADATA,  # 未核准
            "ce": APPROVED_CE_METADATA,
        },
    )
    router = AsrRouter(
        config, providers={"formo_remote": unapproved, "ce_remote": approved}
    )

    outcome = route(router)

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert unapproved.calls == 0
    assert approved.calls == 0
    assert outcome.attempt_count == 0
    assert outcome.failover_occurred is False


def test_disabled_primary_does_not_fall_through_to_backup() -> None:
    disabled = SpyProvider("ce_remote", Transcript(text="不該被用到"))
    config = build_config(
        primary="ce_remote",
        fallback=("hak_mock",),
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.DISABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
            "hak_mock": ProviderConfig(
                identifier="hak_mock", status=ProviderStatus.ENABLED
            ),
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(config, providers={"ce_remote": disabled})

    outcome = route(router)

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert outcome.attempt_count == 0
    assert disabled.calls == 0


def test_disabled_transcribe_primary_does_not_fall_through_to_ce() -> None:
    """managed 主力停用是 route gate 失敗，不能視為 provider runtime 錯誤。"""
    transcribe = SpyProvider(
        AMAZON_TRANSCRIBE_PROVIDER_ID, Transcript(text="不該被用到")
    )
    ce = SpyProvider("ce_remote", Transcript(text="不該被用到"))
    config = build_config(
        primary=AMAZON_TRANSCRIBE_PROVIDER_ID,
        fallback=("ce_remote",),
        language="zh-TW",
        providers={
            AMAZON_TRANSCRIBE_PROVIDER_ID: ProviderConfig(
                identifier=AMAZON_TRANSCRIBE_PROVIDER_ID,
                status=ProviderStatus.DISABLED,
                kind=ProviderKind.AWS_MANAGED,
            ),
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(
        config,
        providers={AMAZON_TRANSCRIBE_PROVIDER_ID: transcribe, "ce_remote": ce},
    )

    outcome = route(router, Language.ZH_TW)

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert transcribe.calls == 0
    assert ce.calls == 0
    assert outcome.attempt_count == 0


def test_provider_declared_without_instance_is_ineligible() -> None:
    """設定宣告了 provider 但 composition root 沒建立實例 → 不可用。"""
    config = build_config(
        primary="ce_remote",
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            )
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(config)  # 未注入 ce_remote 實例

    outcome = route(router)

    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED


def test_remote_model_kind_without_metadata_reference_is_ineligible() -> None:
    """宣告為遠端模型卻沒綁 metadata，等於沒有核准依據，一律拒絕。"""
    spy = SpyProvider("ce_remote", Transcript(text="不該被用到"))
    config = build_config(
        primary="ce_remote",
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            )
        },
    )
    router = AsrRouter(config, providers={"ce_remote": spy})

    outcome = route(router)

    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert spy.calls == 0


def test_metadata_reference_pointing_to_missing_metadata_is_ineligible() -> None:
    spy = SpyProvider("ce_remote", Transcript(text="不該被用到"))
    config = build_config(
        primary="ce_remote",
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="does_not_exist",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            )
        },
    )
    router = AsrRouter(config, providers={"ce_remote": spy})

    outcome = route(router)

    assert outcome.result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert spy.calls == 0


# ─────────────────────────────────────────────────────────────────
# 執行期備援
# ─────────────────────────────────────────────────────────────────
def test_runtime_provider_failure_falls_over_to_backup() -> None:
    """主力執行後失敗 → router 改用備援，並回報 failover。"""
    failing = SpyProvider(
        "ce_remote",
        TypedAsrError(
            category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
            message="safe",
            retryable=True,
        ),
    )
    config = build_config(
        primary="ce_remote",
        fallback=("hak_mock",),
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
            "hak_mock": ProviderConfig(
                identifier="hak_mock", status=ProviderStatus.ENABLED
            ),
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(config, providers={"ce_remote": failing})

    outcome = route(router)

    assert isinstance(outcome.result, Transcript)
    assert failing.calls == 1
    assert outcome.attempt_count == 2
    assert outcome.failover_occurred is True
    assert outcome.served_provider_id == "hak_mock"


def test_transcribe_unavailable_falls_over_to_approved_ce() -> None:
    """中文 managed 主力暫時不可用時，才嘗試已核准的 CE 備援。"""
    transcribe = SpyProvider(
        AMAZON_TRANSCRIBE_PROVIDER_ID,
        TypedAsrError(
            category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
            message="safe",
            retryable=True,
        ),
    )
    ce = SpyProvider("ce_remote", Transcript(text="CE 備援結果"))
    config = build_config(
        primary=AMAZON_TRANSCRIBE_PROVIDER_ID,
        fallback=("ce_remote",),
        language="zh-TW",
        providers={
            AMAZON_TRANSCRIBE_PROVIDER_ID: ProviderConfig(
                identifier=AMAZON_TRANSCRIBE_PROVIDER_ID,
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.AWS_MANAGED,
            ),
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(
        config,
        providers={AMAZON_TRANSCRIBE_PROVIDER_ID: transcribe, "ce_remote": ce},
    )

    outcome = route(router, Language.ZH_TW)

    assert isinstance(outcome.result, Transcript)
    assert outcome.result.text == "CE 備援結果"
    assert transcribe.calls == 1
    assert ce.calls == 1
    assert outcome.attempted_provider_ids == (
        AMAZON_TRANSCRIBE_PROVIDER_ID,
        "ce_remote",
    )
    assert outcome.failover_occurred is True


@pytest.mark.parametrize(
    "category",
    [
        AsrErrorCategory.INVALID_AUDIO,
        AsrErrorCategory.UNSUPPORTED_LANGUAGE,
        AsrErrorCategory.ROUTE_NOT_APPROVED,
        AsrErrorCategory.CANCELLED,
        AsrErrorCategory.DEADLINE_EXCEEDED,
    ],
)
def test_terminal_provider_errors_do_not_fall_over(
    category: AsrErrorCategory,
) -> None:
    """輸入、核准、取消與逾期錯誤不得靠備援繞過。"""
    terminal_provider = SpyProvider(
        "ce_remote",
        TypedAsrError(
            category=category,
            message="safe",
            retryable=category is AsrErrorCategory.DEADLINE_EXCEEDED,
        ),
    )
    backup = SpyProvider("hak_mock_backup", Transcript(text="不該被用到"))
    config = build_config(
        primary="ce_remote",
        fallback=("hak_mock_backup",),
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
            "hak_mock_backup": ProviderConfig(
                identifier="hak_mock_backup", status=ProviderStatus.ENABLED
            ),
        },
        metadata={"ce": APPROVED_CE_METADATA},
    )
    router = AsrRouter(
        config,
        providers={"ce_remote": terminal_provider, "hak_mock_backup": backup},
    )

    outcome = route(router)

    assert isinstance(outcome.result, TypedAsrError)
    assert outcome.result.category is category
    assert backup.calls == 0


# ─────────────────────────────────────────────────────────────────
# 公開讀取介面
# ─────────────────────────────────────────────────────────────────
def test_route_config_for_exposes_route_without_private_access() -> None:
    config = build_config(
        primary="hak_mock",
        providers={
            "hak_mock": ProviderConfig(
                identifier="hak_mock", status=ProviderStatus.ENABLED
            )
        },
    )
    router = AsrRouter(config)

    assert router.route_config_for(Language.HAK) is config.routes["hak"]
    assert router.route_config_for(Language.ZH_TW) is None
    assert router.route_config_for("not-a-language") is None  # type: ignore[arg-type]
    assert router.config is config
