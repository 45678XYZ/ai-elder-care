"""
Property 5: Evidence/ADR schema、終態一致性與去識別化。

**Validates: Requirements 6.5, 6.10, 7.1, 7.2, 7.3, 7.5, 8.11; Design Property 5**

For all 結構化 evidence records，當 record 成功時它必須具有
`transcript_present=true` 與正的 `transcript_character_count`，
當 record 失敗時它必須具有非空白 `failure_prerequisite` 與 `failure_category`；
for any record 或 ADR reference 含完整 transcript、token、audio、prompt ID、
raw response、缺必填欄位或未允許 reference key，schema validation 必須拒絕它；
for any 被接受的 ADR evidence reference，它只能投影 `run_id`、`model_id`、
`input_fixture_id`、`outcome`、`failure_category`。
"""
from __future__ import annotations

import re
import string
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.shared.asr.evidence import (
    ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS,
    ADR_MANDATORY_HEADINGS,
    EVIDENCE_REDACTED_FIELDS,
    EVIDENCE_REQUIRED_FIELDS,
    EvidenceValidationError,
    validate_adr_evidence_reference,
    validate_adr_template_headings,
    validate_evidence_record,
)


# ─────────────────────────────────────────────────────────────────
# Strategies — Evidence Records
# ─────────────────────────────────────────────────────────────────
def _non_blank_text() -> st.SearchStrategy[str]:
    """Generate non-blank text (at least 1 non-whitespace character)."""
    return st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P"),
            min_codepoint=0x21,
            max_codepoint=0x7E,
        ),
    )


def _positive_int() -> st.SearchStrategy[int]:
    """Generate positive integers."""
    return st.integers(min_value=1, max_value=100_000)


@st.composite
def valid_success_evidence_records(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate valid evidence records with outcome=success.
    success requires transcript_present=True and transcript_character_count > 0.
    """
    record: dict[str, Any] = {
        "schema_version": draw(_non_blank_text()),
        "run_id": draw(_non_blank_text()),
        "recorded_at": draw(_non_blank_text()),
        "model_id": draw(_non_blank_text()),
        "model_revision": draw(_non_blank_text()),
        "language": draw(st.sampled_from(["zh-TW", "hak", "en"])),
        "input_format": draw(st.sampled_from(["wav", "m4a"])),
        "input_fixture_id": draw(_non_blank_text()),
        "audio_duration_ms": draw(_positive_int()),
        "runtime_kind": draw(_non_blank_text()),
        "dependency_manifest_digest": draw(_non_blank_text()),
        "outcome": "success",
        "failure_prerequisite": draw(_non_blank_text()),
        "failure_category": draw(_non_blank_text()),
        "transcript_present": True,
        "transcript_character_count": draw(_positive_int()),
        "evidence_redaction_version": draw(_non_blank_text()),
    }
    return record


@st.composite
def valid_failure_evidence_records(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate valid evidence records with outcome=failure.
    failure requires non-blank failure_prerequisite and failure_category.
    """
    record: dict[str, Any] = {
        "schema_version": draw(_non_blank_text()),
        "run_id": draw(_non_blank_text()),
        "recorded_at": draw(_non_blank_text()),
        "model_id": draw(_non_blank_text()),
        "model_revision": draw(_non_blank_text()),
        "language": draw(st.sampled_from(["zh-TW", "hak", "en"])),
        "input_format": draw(st.sampled_from(["wav", "m4a"])),
        "input_fixture_id": draw(_non_blank_text()),
        "audio_duration_ms": draw(_positive_int()),
        "runtime_kind": draw(_non_blank_text()),
        "dependency_manifest_digest": draw(_non_blank_text()),
        "outcome": "failure",
        "failure_prerequisite": draw(_non_blank_text()),
        "failure_category": draw(_non_blank_text()),
        "transcript_present": draw(st.sampled_from([True, False])),
        "transcript_character_count": draw(st.integers(min_value=0, max_value=100)),
        "evidence_redaction_version": draw(_non_blank_text()),
    }
    return record


@st.composite
def evidence_records_missing_required_field(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate an evidence record with at least one required field removed.
    """
    # Start with a valid success record
    record = draw(valid_success_evidence_records())
    # Remove at least one required field
    field_to_remove = draw(st.sampled_from(sorted(EVIDENCE_REQUIRED_FIELDS)))
    del record[field_to_remove]
    return record


@st.composite
def evidence_records_with_redacted_field(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate an evidence record that includes at least one redacted/forbidden field.
    """
    # Start with a valid success record
    record = draw(valid_success_evidence_records())
    # Add a forbidden field
    redacted_field = draw(st.sampled_from(sorted(EVIDENCE_REDACTED_FIELDS)))
    record[redacted_field] = draw(_non_blank_text())
    return record


# ─────────────────────────────────────────────────────────────────
# Strategies — ADR Evidence References
# ─────────────────────────────────────────────────────────────────
@st.composite
def valid_adr_references(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate ADR evidence references containing only allowed keys.
    Must use at least one key from the allowed set.
    """
    # Pick a non-empty subset of allowed keys
    keys = draw(
        st.lists(
            st.sampled_from(sorted(ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS)),
            min_size=1,
            max_size=len(ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS),
            unique=True,
        )
    )
    ref: dict[str, Any] = {}
    for key in keys:
        ref[key] = draw(_non_blank_text())
    return ref


@st.composite
def adr_references_with_disallowed_keys(draw: st.DrawFn) -> dict[str, Any]:
    """
    Generate ADR evidence references that contain at least one disallowed key.
    """
    # Start with some allowed keys
    ref = draw(valid_adr_references())
    # Add a disallowed key (choose from redacted fields or arbitrary non-allowed key)
    disallowed_candidates = sorted(
        EVIDENCE_REDACTED_FIELDS
        | {"extra_field", "secret", "audio_bytes", "full_transcript"}
    )
    disallowed_key = draw(st.sampled_from(disallowed_candidates))
    ref[disallowed_key] = draw(_non_blank_text())
    return ref


# ─────────────────────────────────────────────────────────────────
# Strategies — ADR Template Headings
# ─────────────────────────────────────────────────────────────────
def _heading_level() -> st.SearchStrategy[str]:
    """Generate markdown heading prefix (# to ###)."""
    return st.sampled_from(["#", "##", "###"])


@st.composite
def valid_adr_templates(draw: st.DrawFn) -> str:
    """
    Generate ADR markdown templates containing all mandatory headings.
    """
    lines: list[str] = []
    for heading in ADR_MANDATORY_HEADINGS:
        level = draw(_heading_level())
        # Use the heading name, optionally with different casing/formatting
        heading_text = heading.replace("_", " ").title()
        lines.append(f"{level} {heading_text}")
        # Add some body text
        body = draw(st.text(min_size=0, max_size=30, alphabet=string.ascii_letters))
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


@st.composite
def adr_templates_missing_heading(draw: st.DrawFn) -> tuple[str, str]:
    """
    Generate ADR markdown template with at least one mandatory heading removed.
    Returns (markdown_text, removed_heading).
    """
    headings_list = list(ADR_MANDATORY_HEADINGS)
    # Pick one heading to omit
    omit_heading = draw(st.sampled_from(headings_list))
    lines: list[str] = []
    for heading in headings_list:
        if heading == omit_heading:
            continue
        level = draw(_heading_level())
        heading_text = heading.replace("_", " ").title()
        lines.append(f"{level} {heading_text}")
        lines.append("Some content here.")
        lines.append("")
    return ("\n".join(lines), omit_heading)


# ─────────────────────────────────────────────────────────────────
# Test Class: Valid Success Evidence Records
# ─────────────────────────────────────────────────────────────────
class TestPropertyEvidenceSuccess:
    """
    Valid success evidence records always pass validation.

    **Validates: Requirements 7.1, 7.2**
    """

    @given(record=valid_success_evidence_records())
    def test_valid_success_records_pass_validation(
        self, record: dict[str, Any]
    ) -> None:
        """
        Success records with transcript_present=True and
        transcript_character_count > 0 always pass.

        **Validates: Requirements 7.1, 7.2**
        """
        result = validate_evidence_record(record)
        assert result is record
        assert result["transcript_present"] is True
        assert result["transcript_character_count"] > 0


# ─────────────────────────────────────────────────────────────────
# Test Class: Valid Failure Evidence Records
# ─────────────────────────────────────────────────────────────────
class TestPropertyEvidenceFailure:
    """
    Valid failure evidence records always pass validation.

    **Validates: Requirements 7.1, 7.3**
    """

    @given(record=valid_failure_evidence_records())
    def test_valid_failure_records_pass_validation(
        self, record: dict[str, Any]
    ) -> None:
        """
        Failure records with non-blank failure_prerequisite and
        failure_category always pass.

        **Validates: Requirements 7.1, 7.3**
        """
        result = validate_evidence_record(record)
        assert result is record
        assert result["outcome"] == "failure"
        assert isinstance(result["failure_prerequisite"], str)
        assert result["failure_prerequisite"].strip() != ""
        assert isinstance(result["failure_category"], str)
        assert result["failure_category"].strip() != ""


# ─────────────────────────────────────────────────────────────────
# Test Class: Missing Required Fields
# ─────────────────────────────────────────────────────────────────
class TestPropertyMissingFields:
    """
    Records missing any required field are rejected.

    **Validates: Requirements 7.1, 6.5**
    """

    @given(record=evidence_records_missing_required_field())
    def test_missing_required_field_raises_validation_error(
        self, record: dict[str, Any]
    ) -> None:
        """
        Any record missing at least one required field must be rejected
        with EvidenceValidationError.

        **Validates: Requirements 7.1, 6.5**
        """
        with pytest.raises(EvidenceValidationError):
            validate_evidence_record(record)


# ─────────────────────────────────────────────────────────────────
# Test Class: Redacted Fields Rejection
# ─────────────────────────────────────────────────────────────────
class TestPropertyRedactedFields:
    """
    Records containing any redacted/forbidden field are rejected.

    **Validates: Requirements 6.10, 7.5, 8.11**
    """

    @given(record=evidence_records_with_redacted_field())
    def test_redacted_field_raises_validation_error(
        self, record: dict[str, Any]
    ) -> None:
        """
        Any record containing transcript, token, audio, prompt_id,
        raw_response or other redacted fields must be rejected.

        **Validates: Requirements 6.10, 7.5, 8.11**
        """
        with pytest.raises(EvidenceValidationError):
            validate_evidence_record(record)


# ─────────────────────────────────────────────────────────────────
# Test Class: ADR Evidence Reference Projection
# ─────────────────────────────────────────────────────────────────
class TestPropertyAdrReference:
    """
    ADR evidence references: only allowed keys pass; disallowed keys rejected.

    **Validates: Requirements 7.2, 7.3, 7.5**
    """

    @given(ref=valid_adr_references())
    def test_references_with_only_allowed_keys_pass(
        self, ref: dict[str, Any]
    ) -> None:
        """
        References containing only run_id, model_id, input_fixture_id,
        outcome, failure_category pass validation.

        **Validates: Requirements 7.2, 7.3**
        """
        result = validate_adr_evidence_reference(ref)
        assert result is ref
        # All keys must be in allowed set
        assert set(result.keys()) <= ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS

    @given(ref=adr_references_with_disallowed_keys())
    def test_references_with_disallowed_keys_are_rejected(
        self, ref: dict[str, Any]
    ) -> None:
        """
        References containing any key not in the five-key allowlist
        must be rejected.

        **Validates: Requirements 7.5, 8.11**
        """
        with pytest.raises(EvidenceValidationError):
            validate_adr_evidence_reference(ref)


# ─────────────────────────────────────────────────────────────────
# Test Class: ADR Template Headings Validation
# ─────────────────────────────────────────────────────────────────
class TestPropertyAdrTemplate:
    """
    ADR templates with all mandatory headings pass; missing heading rejected.

    **Validates: Requirements 6.5, 7.1**
    """

    @given(template=valid_adr_templates())
    def test_templates_with_all_headings_pass(self, template: str) -> None:
        """
        Templates containing all mandatory headings pass validation.

        **Validates: Requirements 6.5, 7.1**
        """
        result = validate_adr_template_headings(template)
        assert result == []

    @given(data=adr_templates_missing_heading())
    def test_templates_missing_heading_are_rejected(
        self, data: tuple[str, str]
    ) -> None:
        """
        Templates missing any mandatory heading must raise
        EvidenceValidationError.

        **Validates: Requirements 6.5, 7.1**
        """
        template, omitted = data
        with pytest.raises(EvidenceValidationError):
            validate_adr_template_headings(template)
