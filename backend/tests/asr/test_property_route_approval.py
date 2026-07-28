"""
Property-based test: 未核准路由永遠 fail closed。

**Validates: Requirements 3.4, 3.7, 4.4, 4.5, 8.7; Design Property 1**

驗證三大情境下 Router 必須 fail-closed：
1. zh-TW AWS capability-gate 任一缺少 → route_not_approved 且 zero transport call
2. CE/Formo production route → route_not_approved 且 zero transport call
3. 不在 language allowlist 的值 → unsupported_language 且 zero transport call
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hypothesis.strategies as st
from hypothesis import given, settings

from src.shared.asr.config import (
    AsrConfig,
    AwsCapabilityGate,
    CE_MODEL_METADATA,
    FORMO_MODEL_METADATA,
    ModelMetadata,
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
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Shared constants and helpers
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
# Transport call spy — verifies zero-call invariant
# ─────────────────────────────────────────────────────────────────
@dataclass
class TransportCallSpy:
    """Spy that tracks if any transport call was made. Should remain at zero calls."""

    call_count: int = 0

    def transcribe(self, request: Any) -> str:
        self.call_count += 1
        return "should not be called"


# ─────────────────────────────────────────────────────────────────
# Hypothesis Strategies
# ─────────────────────────────────────────────────────────────────

# Strategy: Generate an incomplete capability gate (at least one field is False)
@st.composite
def incomplete_capability_gate(draw: st.DrawFn) -> AwsCapabilityGate:
    """Generate a gate where at least one of the 9 items is False."""
    gate_items = [
        "region_zh_tw_support",
        "service_input_output_mode",
        "canonical_pcm_compatibility",
        "timeout_behavior",
        "cancellation_behavior",
        "iam_permissions",
        "s3_necessity",
        "s3_result_handling",
        "s3_cleanup_requirement",
    ]

    # Generate 9 booleans
    values = draw(st.lists(st.booleans(), min_size=9, max_size=9))

    # Ensure at least one is False
    if all(values):
        # Pick a random index to flip to False
        idx = draw(st.integers(min_value=0, max_value=8))
        values[idx] = False

    kwargs = dict(zip(gate_items, values))
    # Optionally add approval_record_ref
    ref = draw(st.one_of(st.none(), st.text(min_size=1, max_size=10)))
    kwargs["approval_record_ref"] = ref

    return AwsCapabilityGate(**kwargs)


# Strategy: Generate a CE or Formo model metadata reference for a provider
@st.composite
def ce_or_formo_route_config(draw: st.DrawFn) -> AsrConfig:
    """Generate a config where zh-TW route points to CE or Formo model."""
    # Choose CE or Formo
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
            ),
        },
        model_metadata={
            metadata_ref: metadata,
        },
        aws_capability_gate=AwsCapabilityGate.default_incomplete(),
        formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
    )


# Strategy: Generate unknown language strings (not "zh-TW" or "hak")
@st.composite
def unknown_language_string(draw: st.DrawFn) -> str:
    """Generate a string that is NOT 'zh-TW' or 'hak'."""
    lang = draw(
        st.text(min_size=1, max_size=20).filter(
            lambda x: x not in ("zh-TW", "hak")
        )
    )
    return lang


# ─────────────────────────────────────────────────────────────────
# Property Tests
# ─────────────────────────────────────────────────────────────────
class TestPropertyRouteApproval:
    """
    Property 1: 未核准路由永遠 fail closed。

    **Validates: Requirements 3.4, 3.7, 4.4, 4.5, 8.7; Design Property 1**
    """

    @given(gate=incomplete_capability_gate())
    @settings(max_examples=100)
    def test_incomplete_gate_returns_route_not_approved(
        self, gate: AwsCapabilityGate
    ) -> None:
        """
        zh-TW 路由搭配任一 capability gate 缺項 → route_not_approved 且 zero call。

        **Validates: Requirements 3.4, 3.7**
        """
        config = AsrConfig(
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
            aws_capability_gate=gate,
            formo_prompt_id_allowlist=_FORMO_ALLOWLIST,
        )

        spy = TransportCallSpy()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        # Must be route_not_approved
        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError but got {type(result).__name__}"
        )
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED, (
            f"Expected ROUTE_NOT_APPROVED but got {result.category}"
        )
        # Transport spy must have zero calls (router blocks before provider)
        assert spy.call_count == 0, (
            f"Expected zero transport calls but got {spy.call_count}"
        )

    @given(config=ce_or_formo_route_config())
    @settings(max_examples=100)
    def test_ce_formo_production_route_returns_route_not_approved(
        self, config: AsrConfig
    ) -> None:
        """
        CE 或 Formo 模型的 production route → route_not_approved 且 zero call。

        **Validates: Requirements 4.4, 4.5**
        """
        spy = TransportCallSpy()
        router = AsrRouter(config)

        result = router.route(
            audio=_make_canonical_audio(),
            language=Language.ZH_TW,
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        # Must be route_not_approved
        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError but got {type(result).__name__}"
        )
        assert result.category == AsrErrorCategory.ROUTE_NOT_APPROVED, (
            f"Expected ROUTE_NOT_APPROVED but got {result.category}"
        )
        # Transport spy must have zero calls
        assert spy.call_count == 0, (
            f"Expected zero transport calls but got {spy.call_count}"
        )

    @given(lang_str=unknown_language_string())
    @settings(max_examples=100)
    def test_unknown_language_returns_unsupported_language(
        self, lang_str: str
    ) -> None:
        """
        不在 language allowlist（非 zh-TW 或 hak）→ unsupported_language 且 zero call。

        **Validates: Requirements 8.7**
        """
        # Use a minimal config — language check happens first regardless
        config = AsrConfig(
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

        spy = TransportCallSpy()
        router = AsrRouter(config)

        # Pass the raw string (not Language enum) — this simulates unknown language
        result = router.route(
            audio=_make_canonical_audio(),
            language=lang_str,  # type: ignore[arg-type]
            deadline=_make_deadline_not_expired(),
            cancellation=_make_cancellation_not_triggered(),
            context=_make_context(),
        )

        # Must be unsupported_language
        assert isinstance(result, TypedAsrError), (
            f"Expected TypedAsrError but got {type(result).__name__}"
        )
        assert result.category == AsrErrorCategory.UNSUPPORTED_LANGUAGE, (
            f"Expected UNSUPPORTED_LANGUAGE but got {result.category}"
        )
        # Transport spy must have zero calls
        assert spy.call_count == 0, (
            f"Expected zero transport calls but got {spy.call_count}"
        )
