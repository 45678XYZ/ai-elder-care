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
    CE_MODEL_METADATA,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    AccessStatus,
    ApprovalState,
    UsageRestriction,
    ProviderConfig,
    ProviderKind,
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
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


def _make_zh_tw_config_gate_incomplete() -> AsrConfig:
    """Config with zh-TW route enabled but remote model unapproved."""
    return AsrConfig(
        routes={
            "zh-TW": RouteConfig(
                route="zh-TW", provider_identifier="ce_remote", enabled=True
            ),
        },
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.REMOTE_MODEL,
                metadata_ref="ce_meta",
                endpoint_name="ep-ce",
            ),
        },
        model_metadata={
            "ce_meta": CE_MODEL_METADATA,  # 未核准
        },
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


def _make_zh_tw_config_gate_complete() -> AsrConfig:
    """Config with zh-TW route enabled and remote model approved."""
    from src.shared.asr.config import ModelProductionGate

    approved_gate = ModelProductionGate(
        colab_validation_passed=True,
        license_cleared=True,
        access_granted=True,
        quota_cleared=True,
        runtime_capacity_verified=True,
    )
    approved_ce = ModelMetadata(
        model_id=CE_MODEL_METADATA.model_id,
        revision=CE_MODEL_METADATA.revision,
        license=CE_MODEL_METADATA.license,
        access_status=AccessStatus.OPEN,
        usage_restriction=UsageRestriction.PRODUCTION,
        approval_state=ApprovalState.APPROVED,
        production_gate=approved_gate,
    )
    return AsrConfig(
        routes={
            "zh-TW": RouteConfig(
                route="zh-TW", provider_identifier="ce_remote", enabled=True
            ),
        },
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.REMOTE_MODEL,
                metadata_ref="ce_meta",
                endpoint_name="ep-ce",
            ),
        },
        model_metadata={
            "ce_meta": approved_ce,
        },
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
# Tests: CE/Formo production route prohibition (unapproved gate)
# ─────────────────────────────────────────────────────────────────
class TestRouterCeFormoProhibition:
    """CE/Formo production route with unapproved gate → route_not_approved。"""

    def test_ce_model_route_prohibited(self) -> None:
        """Route 使用 CE model 但未核准 → route_not_approved。"""
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
                    kind=ProviderKind.REMOTE_MODEL,
                    endpoint_name="ep-ce",
                ),
            },
            model_metadata={
                "ce_meta": CE_MODEL_METADATA,
            },
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
        """Route 使用 Formo model 但未核准 → route_not_approved。"""
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
                    kind=ProviderKind.REMOTE_MODEL,
                    endpoint_name="ep-formo",
                ),
            },
            model_metadata={
                "formo_meta": FORMO_MODEL_METADATA,
            },
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
# Tests: zh-TW unapproved remote model
# ─────────────────────────────────────────────────────────────────
class TestRouterZhTwUnapprovedModel:
    """zh-TW: unapproved remote model → route_not_approved。"""

    def test_unapproved_remote_returns_route_not_approved(self) -> None:
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

        class _Spy:
            provider_id = "ce_remote"
            def transcribe(self, *a, **kw):
                raise AssertionError("should not be called")

        router = AsrRouter(config, providers={"ce_remote": _Spy()})

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

        class _Spy:
            provider_id = "ce_remote"
            def transcribe(self, *a, **kw):
                raise AssertionError("should not be called")

        router = AsrRouter(config, providers={"ce_remote": _Spy()})

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
        """Unapproved model takes precedence over cancellation."""
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

        class _Spy:
            provider_id = "ce_remote"
            def transcribe(self, *a, **kw):
                raise AssertionError("should not be called")

        router = AsrRouter(config, providers={"ce_remote": _Spy()})

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_expired(),
            cancellation=_make_cancellation_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError)
        assert result.category == AsrErrorCategory.CANCELLED
