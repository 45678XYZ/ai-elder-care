"""
Property-based test: 未核准路由永遠 fail closed（remote-only 架構）。

驗證三大情境下 Router 必須 fail-closed：
1. CE/Formo 的 production gate 未逐項核准 → route_not_approved 且 zero provider call
2. 不在 language allowlist 的值 → unsupported_language 且 zero provider call
3. 遠端 provider endpoint 缺失 → route_not_approved

remote-only 架構下不再有 AWS capability gate 檢查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hypothesis.strategies as st
from hypothesis import given, settings

from src.shared.asr.config import (
    AsrConfig,
    CE_MODEL_METADATA,
    FORMO_MODEL_METADATA,
    ModelMetadata,
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
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Shared constants and helpers
# ─────────────────────────────────────────────────────────────────
def _make_canonical_audio() -> CanonicalAudio:
    return CanonicalAudio(
        pcm_s16le=b"\x00\x00" * 160,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bits=16,
        duration_ms=10,
        input_format=InputFormat.WAV,
    )


def _make_context() -> CorrelationContext:
    return CorrelationContext(correlation_id="prop-test-route-approval")


def _make_deadline_not_expired() -> Deadline:
    return Deadline.create(expiry=999.0, clock=lambda: 0.0)


def _make_cancellation_not_triggered() -> CancellationSignal:
    return CancellationSignal()


# ─────────────────────────────────────────────────────────────────
# Provider call spy — verifies zero-call invariant
# ─────────────────────────────────────────────────────────────────
@dataclass
class ProviderCallSpy:
    """Spy that tracks if any provider call was made."""

    call_count: int = 0
    provider_id: str = "spy"

    def transcribe(self, audio, language, deadline, cancellation, context):
        self.call_count += 1
        return TypedAsrError(
            category=AsrErrorCategory.PROVIDER_FAILURE,
            message="spy should not be called",
            retryable=False,
        )


# ─────────────────────────────────────────────────────────────────
# Hypothesis Strategies
# ─────────────────────────────────────────────────────────────────

# Strategy: Generate a CE or Formo model metadata reference for a provider
@st.composite
def ce_or_formo_route_config(draw: st.DrawFn) -> AsrConfig:
    """Generate a config where zh-TW route points to unapproved CE or Formo model."""
    metadata = draw(st.sampled_from([CE_MODEL_METADATA, FORMO_MODEL_METADATA]))
    provider_id = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll",)),
            min_size=3,
            max_size=10,
        )
    )
    metadata_ref = f"{provider_id}_meta"

    return AsrConfig(
        routes={
            "zh-TW": RouteConfig(
                route="zh-TW", provider_identifier=provider_id, enabled=True
            ),
        },
        providers={
            provider_id: ProviderConfig(
                identifier=provider_id,
                status=ProviderStatus.ENABLED,
                metadata_ref=metadata_ref,
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name=f"ep-{provider_id}",
            ),
        },
        model_metadata={
            metadata_ref: metadata,
        },
    )


# Strategy: Generate unknown language strings (not "zh-TW" or "hak")
@st.composite
def unknown_language_string(draw: st.DrawFn) -> str:
    """Generate a string that is NOT 'zh-TW' or 'hak'."""
    return draw(
        st.text(min_size=1, max_size=20).filter(
            lambda x: x not in ("zh-TW", "hak")
        )
    )


# ─────────────────────────────────────────────────────────────────
# Property Tests
# ─────────────────────────────────────────────────────────────────
class TestPropertyRouteApproval:
    """
    Property: 未核准路由永遠 fail closed（remote-only 架構）。
    """

    @given(config=ce_or_formo_route_config())
    @settings(max_examples=100)
    def test_ce_formo_production_route_returns_route_not_approved(
        self, config: AsrConfig
    ) -> None:
        """
        CE 或 Formo 模型未核准 → route_not_approved 且 zero provider call。
        """
        spy = ProviderCallSpy()
        router = AsrRouter(config, providers={
            list(config.providers.keys())[0]: spy,
        })

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError but got {type(result).__name__}"
        )
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED, (
            f"Expected ROUTE_NOT_APPROVED but got {result.category}"
        )
        assert spy.call_count == 0, (
            f"Expected zero provider calls but got {spy.call_count}"
        )

    @given(lang_str=unknown_language_string())
    @settings(max_examples=100)
    def test_unknown_language_returns_unsupported_language(
        self, lang_str: str
    ) -> None:
        """
        不在 language allowlist（非 zh-TW 或 hak）→ unsupported_language。
        """
        config = AsrConfig(
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
                "ce_meta": CE_MODEL_METADATA,
            },
        )

        spy = ProviderCallSpy(provider_id="ce_remote")
        router = AsrRouter(config, providers={"ce_remote": spy})

        result = router.route(
            audio=_make_canonical_audio(),
            language=lang_str,  # type: ignore[arg-type]
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError but got {type(result).__name__}"
        )
        assert result.category == AsrErrorCategory.UNSUPPORTED_LANGUAGE, (
            f"Expected UNSUPPORTED_LANGUAGE but got {result.category}"
        )
        assert spy.call_count == 0
