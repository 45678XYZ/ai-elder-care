"""
Property-based tests: Formo Prompt ID 精確 allowlist。

**Validates: Requirements 3.8, 6.6, 8.10; Design Property 4**

Property 4: 對任意 Unicode 字串作為 Formo Prompt ID，設定與 Colab validation input
當且僅當它精確等於六個允許值時才可接受；所有其他字串都必須被拒絕，
且不得被寫入 Safe Telemetry、evidence 或 ADR。
"""
from __future__ import annotations

import pytest
from hypothesis import given, assume
from hypothesis import strategies as st

from src.shared.asr.config import validate_formo_prompt_id
from src.shared.asr.telemetry import TELEMETRY_ALLOWLIST_KEYS, SafeTelemetryRecord
from src.shared.asr.evidence import (
    EVIDENCE_REDACTED_FIELDS,
    EVIDENCE_REQUIRED_FIELDS,
    ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS,
    EvidenceValidationError,
    validate_evidence_record,
    validate_adr_evidence_reference,
)


# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────
VALID_PROMPT_IDS: list[str] = [
    "htia_sixian",
    "htia_hailu",
    "htia_dapu",
    "htia_raoping",
    "htia_zhaoan",
    "htia_nansixian",
]

_VALID_SET: frozenset[str] = frozenset(VALID_PROMPT_IDS)


# ─────────────────────────────────────────────────────────────────
# Helpers — minimal valid evidence record for injection tests
# ─────────────────────────────────────────────────────────────────
def _make_success_record() -> dict[str, object]:
    """Build a minimal evidence record with outcome=success that passes validation without prompt fields."""
    return {
        "schema_version": "1.0",
        "run_id": "run-001",
        "recorded_at": "2025-01-01T00:00:00Z",
        "model_id": "test-model",
        "model_revision": "v1",
        "language": "hak",
        "input_format": "wav",
        "input_fixture_id": "fixture-001",
        "audio_duration_ms": 3000,
        "runtime_kind": "colab",
        "dependency_manifest_digest": "sha256:abc",
        "outcome": "success",
        "failure_prerequisite": "",
        "failure_category": "",
        "transcript_present": True,
        "transcript_character_count": 10,
        "evidence_redaction_version": "1",
    }


# ─────────────────────────────────────────────────────────────────
# Property 4a: validate_formo_prompt_id accepts IFF exact match
# ─────────────────────────────────────────────────────────────────
class TestFormoPromptAllowlistProperty:
    """Formo Prompt ID 精確 allowlist property tests."""

    @given(candidate=st.text(min_size=0, max_size=200))
    def test_arbitrary_unicode_accepted_iff_exact_match(self, candidate: str) -> None:
        """
        任意 Unicode candidate，validate_formo_prompt_id 接受當且僅當
        candidate 精確等於六個允許值之一。

        **Validates: Requirements 3.8, 6.6, 8.10; Design Property 4**
        """
        if candidate in _VALID_SET:
            result = validate_formo_prompt_id(candidate)
            assert result == candidate
        else:
            with pytest.raises(ValueError):
                validate_formo_prompt_id(candidate)

    @given(valid_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_valid_ids_always_accepted_and_returned_unchanged(self, valid_id: str) -> None:
        """
        六個有效 ID 總是被接受且回傳值完全等於輸入（無正規化）。

        **Validates: Requirements 3.8; Design Property 4**
        """
        result = validate_formo_prompt_id(valid_id)
        assert result == valid_id
        assert result is valid_id or result == valid_id  # 值相等

    @given(
        valid_id=st.sampled_from(VALID_PROMPT_IDS),
        prefix=st.text(min_size=1, max_size=5),
    )
    def test_prefix_variants_rejected(self, valid_id: str, prefix: str) -> None:
        """
        任何在有效 ID 前添加前綴的字串都被拒絕。

        **Validates: Requirements 3.8; Design Property 4**
        """
        candidate = prefix + valid_id
        assume(candidate not in _VALID_SET)
        with pytest.raises(ValueError):
            validate_formo_prompt_id(candidate)

    @given(
        valid_id=st.sampled_from(VALID_PROMPT_IDS),
        suffix=st.text(min_size=1, max_size=5),
    )
    def test_suffix_variants_rejected(self, valid_id: str, suffix: str) -> None:
        """
        任何在有效 ID 後添加後綴的字串都被拒絕。

        **Validates: Requirements 3.8; Design Property 4**
        """
        candidate = valid_id + suffix
        assume(candidate not in _VALID_SET)
        with pytest.raises(ValueError):
            validate_formo_prompt_id(candidate)

    @given(valid_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_case_variants_rejected(self, valid_id: str) -> None:
        """
        大小寫變形（upper / swapcase）不等於原始值的情況下被拒絕。

        **Validates: Requirements 3.8; Design Property 4**
        """
        upper = valid_id.upper()
        if upper != valid_id:
            with pytest.raises(ValueError):
                validate_formo_prompt_id(upper)

        swapped = valid_id.swapcase()
        if swapped != valid_id:
            with pytest.raises(ValueError):
                validate_formo_prompt_id(swapped)


# ─────────────────────────────────────────────────────────────────
# Property 4b: prompt_id 永遠不出現在 telemetry allowlist
# ─────────────────────────────────────────────────────────────────
class TestFormoPromptNotInTelemetry:
    """Prompt ID 不得出現在 Safe Telemetry 的 allowlist 鍵中。"""

    @given(prompt_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_prompt_id_not_in_telemetry_allowlist(self, prompt_id: str) -> None:
        """
        無論哪個有效 prompt ID，'formo_prompt_id' 與 'prompt_id'
        都不在 TELEMETRY_ALLOWLIST_KEYS 中。

        **Validates: Requirements 6.6, 8.10; Design Property 4**
        """
        assert "formo_prompt_id" not in TELEMETRY_ALLOWLIST_KEYS
        assert "prompt_id" not in TELEMETRY_ALLOWLIST_KEYS
        # prompt_id 值本身也不應在 allowlist keys 中
        assert prompt_id not in TELEMETRY_ALLOWLIST_KEYS

    def test_telemetry_record_to_dict_excludes_prompt_fields(self) -> None:
        """
        SafeTelemetryRecord.to_dict() 輸出鍵不含任何 prompt 相關欄位。

        **Validates: Requirements 6.6; Design Property 4**
        """
        record = SafeTelemetryRecord(
            correlation_id="test-corr-001",
            language="hak",
            route="hakka",
            provider_id="formo",
            input_format="wav",
            canonical_sample_rate_hz=16000,
            canonical_channels=1,
            audio_duration_ms=3000,
            deadline_outcome="not_reached",
            terminal_outcome="success",
            error_category=None,
            elapsed_ms=150,
            retryable=False,
        )
        keys = set(record.to_dict().keys())
        assert "formo_prompt_id" not in keys
        assert "prompt_id" not in keys


# ─────────────────────────────────────────────────────────────────
# Property 4c: Evidence record 含 formo_prompt_id 必被拒絕
# ─────────────────────────────────────────────────────────────────
class TestFormoPromptNotInEvidence:
    """Evidence record 包含 formo_prompt_id 或 prompt_id 欄位時必被拒絕。"""

    @given(prompt_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_evidence_record_with_formo_prompt_id_rejected(self, prompt_id: str) -> None:
        """
        在合法 evidence record 中注入 'formo_prompt_id' 欄位，
        validate_evidence_record 必須拒絕。

        **Validates: Requirements 6.6, 8.10; Design Property 4**
        """
        record = _make_success_record()
        record["formo_prompt_id"] = prompt_id
        with pytest.raises(EvidenceValidationError):
            validate_evidence_record(record)

    @given(prompt_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_evidence_record_with_prompt_id_rejected(self, prompt_id: str) -> None:
        """
        在合法 evidence record 中注入 'prompt_id' 欄位，
        validate_evidence_record 必須拒絕。

        **Validates: Requirements 6.6, 8.10; Design Property 4**
        """
        record = _make_success_record()
        record["prompt_id"] = prompt_id
        with pytest.raises(EvidenceValidationError):
            validate_evidence_record(record)

    def test_formo_prompt_id_in_redacted_fields(self) -> None:
        """
        'formo_prompt_id' 與 'prompt_id' 皆在 EVIDENCE_REDACTED_FIELDS 中。

        **Validates: Requirements 6.6; Design Property 4**
        """
        assert "formo_prompt_id" in EVIDENCE_REDACTED_FIELDS
        assert "prompt_id" in EVIDENCE_REDACTED_FIELDS


# ─────────────────────────────────────────────────────────────────
# Property 4d: ADR evidence-reference 不含 prompt 相關鍵
# ─────────────────────────────────────────────────────────────────
class TestFormoPromptNotInAdr:
    """ADR evidence-reference projection 不允許 prompt 相關鍵。"""

    @given(prompt_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_adr_reference_with_formo_prompt_id_rejected(self, prompt_id: str) -> None:
        """
        ADR reference 包含 'formo_prompt_id' 鍵時，
        validate_adr_evidence_reference 必須拒絕。

        **Validates: Requirements 6.6, 8.10; Design Property 4**
        """
        reference = {
            "run_id": "run-001",
            "formo_prompt_id": prompt_id,
        }
        with pytest.raises(EvidenceValidationError):
            validate_adr_evidence_reference(reference)

    @given(prompt_id=st.sampled_from(VALID_PROMPT_IDS))
    def test_adr_reference_with_prompt_id_rejected(self, prompt_id: str) -> None:
        """
        ADR reference 包含 'prompt_id' 鍵時，
        validate_adr_evidence_reference 必須拒絕。

        **Validates: Requirements 6.6, 8.10; Design Property 4**
        """
        reference = {
            "run_id": "run-001",
            "prompt_id": prompt_id,
        }
        with pytest.raises(EvidenceValidationError):
            validate_adr_evidence_reference(reference)

    def test_prompt_keys_not_in_adr_allowed_keys(self) -> None:
        """
        'formo_prompt_id' 與 'prompt_id' 不在 ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS 中。

        **Validates: Requirements 8.10; Design Property 4**
        """
        assert "formo_prompt_id" not in ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS
        assert "prompt_id" not in ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS
