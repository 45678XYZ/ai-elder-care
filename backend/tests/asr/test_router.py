"""
Tests for ASR Router — router.py。

驗證固定 precedence 路由決策：
- Language validity（unknown → unsupported_language）
- hak route：enabled + hak_mock → success
- hak route：disabled → route_not_approved
- hak route：non-mock provider → route_not_approved
- hak route：missing → route_not_approved
- CE/Formo production route prohibition → route_not_approved
- zh-TW：AWS capability gate incomplete → route_not_approved
- zh-TW：preflight cancellation → cancelled
- zh-TW：preflight deadline → deadline_exceeded
- Fixed precedence order verification
"""
from __future__ import annotations

import pytest

from src.shared.asr.config import (
    AsrConfig,
    AwsCapabilityGate,
    CE_MODEL_METADATA,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    AccessStatus,
    ApprovalState,
    UsageRestriction,
    ProviderConfig,
    ProviderStatus,
    RouteConfig,
)
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


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────
_FORMO_ALLOWLIST = frozenset(
    {
        "htia_sixian",
        "htia_hailu",
        "htia_dapu",
        "htia_raoping",
        "htia_zhaoan",
        "htia_nansixian",
    }
)


def _make_canonical_audio() -> CanonicalAudio:
    """Minimal valid CanonicalAudio for testing."""
    return CanonicalAudio(
        pcm_s16le=b"\x00\x00" * 160,  # 10ms of silence at 16kHz mono 16-bit
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=10,
        input_format=InputFormat.WAV,
    )


def _make_context() -> CorrelationContext:
    return CorrelationContext(correlation_id="test-router")


def _make_deadline_not_expired() -> Deadline:
    return Deadline.create(expiry=999.0, clock=lambda: 0.0)


def _make_deadline_expired() -> Deadline:
    return Deadline.create(expiry=0.0, clock=lambda: 1.0)


def _make_cancellation_not_triggered() -> CancellationSignal:
    return CancellationSignal()


def _make_cancellation_triggered() -> CancellationSignal:
    sig = CancellationSignal()
    sig.trigger()
    return sig


def _make_hak_enabled_config() -> AsrConfig:
    """Config with hak route enabled, provider hak_mock."""
    return AsrConfig(
        routes={
            "hak": RouteConfig(
                route="hak", provider_identifier="hak_mock", enabled=True
            ),
        },
        providers={
            "hak_mock": ProviderConfig(
                identifier="hak_mock", status=ProviderStatus.ENABLED
            ),
        },
        model_metadata={},
        aws_capability_gate=AwsCapabilityGate.default_incomplete(),
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


def _make_zh_tw_config_gate_incomplete() -> AsrConfig:
    """Config with zh-TW route enabled but gate incomplete."""
    return AsrConfig(
        routes={
            "zh-TW": RouteConfig(
                route="zh-TW", provider_identifier="aws_zh", enabled=True
            ),
        },
        providers={
            "aws_zh": ProviderConfig(
                identifier="aws_zh", status=ProviderStatus.ENABLED
            ),
        },
        model_metadata={},
        aws_capability_gate=AwsCapabilityGate.default_incomplete(),
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


def _make_zh_tw_config_gate_complete() -> AsrConfig:
    """Config with zh-TW route enabled and gate complete."""
    return AsrConfig(
        routes={
            "zh-TW": RouteConfig(
                route="zh-TW", provider_identifier="aws_zh", enabled=True
            ),
        },
        providers={
            "aws_zh": ProviderConfig(
                identifier="aws_zh", status=ProviderStatus.ENABLED
            ),
        },
        model_metadata={},
        aws_capability_gate=AwsCapabilityGate(
            region_zh_tw_support=True,
            service_input_output_mode=True,
            canonical_pcm_compatibility=True,
            timeout_behavior=True,
            cancellation_behavior=True,
            iam_permissions=True,
            s3_necessity=True,
            s3_result_handling=True,
            s3_cleanup_requirement=True,
            approval_record_ref="adr-001",
        ),
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


# ─────────────────────────────────────────────────────────────────
# Tests: Language validity
# ─────────────────────────────────────────────────────────────────
class TestRouterLanguageValidity:
    """Unknown language → unsupported_language。"""

    def test_non_language_enum_returns_unsupported(self) -> None:
        config = _make_hak_enabled_config()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language="en-US",  # type: ignore — deliberately invalid
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.UNSUPPORTED_LANGUAGE


# ─────────────────────────────────────────────────────────────────
# Tests: hak route
# ─────────────────────────────────────────────────────────────────
class TestRouterHakRoute:
    """hak route decisions。"""

    def test_hak_enabled_hak_mock_returns_transcript(self) -> None:
        """hak route: enabled + hak_mock provider → success。"""
        config = _make_hak_enabled_config()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, Transcript)
        assert result.text.strip() != ""

    def test_hak_disabled_returns_route_not_approved(self) -> None:
        """hak route: disabled → route_not_approved。"""
        config = AsrConfig(
            routes={
                "hak": RouteConfig(
                    route="hak", provider_identifier="hak_mock", enabled=False
                ),
            },
            providers={
                "hak_mock": ProviderConfig(
                    identifier="hak_mock", status=ProviderStatus.ENABLED
                ),
            },
            model_metadata={},
            aws_capability_gate=AwsCapabilityGate.default_incomplete(),
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_hak_non_mock_provider_returns_route_not_approved(self) -> None:
        """hak route: non-mock provider → route_not_approved。"""
        config = AsrConfig(
            routes={
                "hak": RouteConfig(
                    route="hak", provider_identifier="some_real_provider", enabled=True
                ),
            },
            providers={
                "some_real_provider": ProviderConfig(
                    identifier="some_real_provider", status=ProviderStatus.ENABLED
                ),
            },
            model_metadata={},
            aws_capability_gate=AwsCapabilityGate.default_incomplete(),
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_hak_missing_route_returns_route_not_approved(self) -> None:
        """hak route: missing → route_not_approved。"""
        config = AsrConfig(
            routes={},  # no hak route
            providers={
                "hak_mock": ProviderConfig(
                    identifier="hak_mock", status=ProviderStatus.ENABLED
                ),
            },
            model_metadata={},
            aws_capability_gate=AwsCapabilityGate.default_incomplete(),
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.HAK,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED


# ─────────────────────────────────────────────────────────────────
# Tests: CE/Formo production route prohibition
# ─────────────────────────────────────────────────────────────────
class TestRouterCeFormoProhibition:
    """CE/Formo production route → route_not_approved。"""

    def test_ce_model_route_prohibited(self) -> None:
        """Route 使用 CE model → route_not_approved。"""
        config = AsrConfig(
            routes={
                "zh-TW": RouteConfig(
                    route="zh-TW", provider_identifier="ce_provider", enabled=True
                ),
            },
            providers={
                "ce_provider": ProviderConfig(
                    identifier="ce_provider",
                    status=ProviderStatus.ENABLED,
                    metadata_ref="ce_meta",
                ),
            },
            model_metadata={
                "ce_meta": CE_MODEL_METADATA,
            },
            aws_capability_gate=AwsCapabilityGate.default_incomplete(),
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_formo_model_route_prohibited(self) -> None:
        """Route 使用 Formo model → route_not_approved。"""
        config = AsrConfig(
            routes={
                "zh-TW": RouteConfig(
                    route="zh-TW", provider_identifier="formo_provider", enabled=True
                ),
            },
            providers={
                "formo_provider": ProviderConfig(
                    identifier="formo_provider",
                    status=ProviderStatus.ENABLED,
                    metadata_ref="formo_meta",
                ),
            },
            model_metadata={
                "formo_meta": FORMO_MODEL_METADATA,
            },
            aws_capability_gate=AwsCapabilityGate.default_incomplete(),
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED


# ─────────────────────────────────────────────────────────────────
# Tests: zh-TW capability gate incomplete
# ─────────────────────────────────────────────────────────────────
class TestRouterZhTwCapabilityGate:
    """zh-TW: AWS capability gate incomplete → route_not_approved。"""

    def test_gate_incomplete_returns_route_not_approved(self) -> None:
        config = _make_zh_tw_config_gate_incomplete()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED


# ─────────────────────────────────────────────────────────────────
# Tests: zh-TW preflight cancellation
# ─────────────────────────────────────────────────────────────────
class TestRouterZhTwPreflightCancellation:
    """zh-TW: preflight cancellation → cancelled。"""

    def test_cancellation_triggered_returns_cancelled(self) -> None:
        config = _make_zh_tw_config_gate_complete()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
        assert result.retryable is False


# ─────────────────────────────────────────────────────────────────
# Tests: zh-TW preflight deadline
# ─────────────────────────────────────────────────────────────────
class TestRouterZhTwPreflightDeadline:
    """zh-TW: preflight deadline → deadline_exceeded。"""

    def test_deadline_expired_returns_deadline_exceeded(self) -> None:
        config = _make_zh_tw_config_gate_complete()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.DEADLINE_EXCEEDED
        assert result.retryable is True


# ─────────────────────────────────────────────────────────────────
# Tests: Fixed precedence order
# ─────────────────────────────────────────────────────────────────
class TestRouterPrecedenceOrder:
    """Fixed precedence: language > route > gate > cancellation > deadline。"""

    def test_language_check_precedes_route_check(self) -> None:
        """Even with missing route, language invalidity takes precedence."""
        config = _make_hak_enabled_config()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language="unknown",  # type: ignore
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.UNSUPPORTED_LANGUAGE

    def test_gate_check_precedes_cancellation(self) -> None:
        """Gate incomplete takes precedence over cancellation."""
        config = _make_zh_tw_config_gate_incomplete()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_cancellation_precedes_deadline(self) -> None:
        """Cancellation takes precedence over deadline."""
        config = _make_zh_tw_config_gate_complete()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_expired(),
            cancellation=_make_cancellation_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
