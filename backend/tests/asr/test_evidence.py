"""
Tests for Evidence Validator 與 ADR Evidence-Reference Projection — evidence.py。

驗證：
- Required fields 檢查
- Redacted fields 檢查
- outcome=success 規則
- outcome=failure 規則
- ADR reference projection 只允許 5 個鍵
"""
from __future__ import annotations

import pytest

from src.shared.asr.evidence import (
    ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS,
    EVIDENCE_REDACTED_FIELDS,
    EVIDENCE_REQUIRED_FIELDS,
    EvidenceValidationError,
    validate_adr_evidence_reference,
    validate_evidence_record,
)


# ─────────────────────────────────────────────────────────────────
# Helper — 建立完整的成功 evidence record
# ─────────────────────────────────────────────────────────────────
def _make_success_record() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "run-abc-123",
        "recorded_at": "2024-01-15T10:30:00Z",
        "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
        "model_revision": "v2.0",
        "language": "zh",
        "input_format": "wav",
        "input_fixture_id": "fixture-001",
        "audio_duration_ms": 5000,
        "runtime_kind": "colab_free_gpu",
        "dependency_manifest_digest": "sha256:abc123",
        "outcome": "success",
        "failure_prerequisite": None,
        "failure_category": None,
        "transcript_present": True,
        "transcript_character_count": 42,
        "evidence_redaction_version": "1",
    }


def _make_failure_record() -> dict:
    record = _make_success_record()
    record["outcome"] = "failure"
    record["failure_prerequisite"] = "gpu_unavailable"
    record["failure_category"] = "runtime_prerequisite_failure"
    record["transcript_present"] = False
    record["transcript_character_count"] = 0
    return record


# ─────────────────────────────────────────────────────────────────
# validate_evidence_record tests
# ─────────────────────────────────────────────────────────────────
class TestValidateEvidenceRecord:
    """Evidence record validation。"""

    def test_valid_success_record(self) -> None:
        record = _make_success_record()
        result = validate_evidence_record(record)
        assert result is record

    def test_valid_failure_record(self) -> None:
        record = _make_failure_record()
        result = validate_evidence_record(record)
        assert result is record

    def test_missing_required_field_raises(self) -> None:
        record = _make_success_record()
        del record["run_id"]
        with pytest.raises(EvidenceValidationError, match="Missing required fields"):
            validate_evidence_record(record)

    def test_missing_multiple_required_fields(self) -> None:
        record = _make_success_record()
        del record["run_id"]
        del record["model_id"]
        with pytest.raises(EvidenceValidationError, match="Missing required fields"):
            validate_evidence_record(record)

    def test_redacted_field_transcript_rejected(self) -> None:
        record = _make_success_record()
        record["transcript"] = "Full transcript text"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_token_rejected(self) -> None:
        record = _make_success_record()
        record["token"] = "hf_abc123"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_hf_token_rejected(self) -> None:
        record = _make_success_record()
        record["hf_token"] = "hf_abc123"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_audio_bytes_rejected(self) -> None:
        record = _make_success_record()
        record["audio_bytes"] = b"\x00\x01"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_pcm_samples_rejected(self) -> None:
        record = _make_success_record()
        record["pcm_samples"] = b"\x00"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_formo_prompt_id_rejected(self) -> None:
        record = _make_success_record()
        record["formo_prompt_id"] = "htia_sixian"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_raw_response_rejected(self) -> None:
        record = _make_success_record()
        record["raw_response"] = {"data": "raw"}
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_raw_provider_response_rejected(self) -> None:
        record = _make_success_record()
        record["raw_provider_response"] = "raw"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_prompt_id_rejected(self) -> None:
        record = _make_success_record()
        record["prompt_id"] = "id"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_redacted_field_audio_rejected(self) -> None:
        record = _make_success_record()
        record["audio"] = b"audio data"
        with pytest.raises(EvidenceValidationError, match="redacted/forbidden"):
            validate_evidence_record(record)

    def test_success_without_transcript_present_true_raises(self) -> None:
        record = _make_success_record()
        record["transcript_present"] = False
        with pytest.raises(
            EvidenceValidationError, match="transcript_present=true"
        ):
            validate_evidence_record(record)

    def test_success_with_transcript_character_count_zero_raises(self) -> None:
        record = _make_success_record()
        record["transcript_character_count"] = 0
        with pytest.raises(
            EvidenceValidationError, match="transcript_character_count > 0"
        ):
            validate_evidence_record(record)

    def test_success_with_transcript_character_count_negative_raises(self) -> None:
        record = _make_success_record()
        record["transcript_character_count"] = -5
        with pytest.raises(
            EvidenceValidationError, match="transcript_character_count > 0"
        ):
            validate_evidence_record(record)

    def test_success_with_non_int_transcript_character_count_raises(self) -> None:
        record = _make_success_record()
        record["transcript_character_count"] = "42"
        with pytest.raises(
            EvidenceValidationError, match="transcript_character_count > 0"
        ):
            validate_evidence_record(record)

    def test_failure_with_blank_failure_prerequisite_raises(self) -> None:
        record = _make_failure_record()
        record["failure_prerequisite"] = ""
        with pytest.raises(
            EvidenceValidationError, match="non-blank failure_prerequisite"
        ):
            validate_evidence_record(record)

    def test_failure_with_blank_failure_category_raises(self) -> None:
        record = _make_failure_record()
        record["failure_category"] = "  "
        with pytest.raises(
            EvidenceValidationError, match="non-blank failure_category"
        ):
            validate_evidence_record(record)

    def test_failure_with_none_failure_prerequisite_raises(self) -> None:
        record = _make_failure_record()
        record["failure_prerequisite"] = None
        with pytest.raises(
            EvidenceValidationError, match="non-blank failure_prerequisite"
        ):
            validate_evidence_record(record)

    def test_failure_with_none_failure_category_raises(self) -> None:
        record = _make_failure_record()
        record["failure_category"] = None
        with pytest.raises(
            EvidenceValidationError, match="non-blank failure_category"
        ):
            validate_evidence_record(record)

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="must be a dict"):
            validate_evidence_record("not a dict")  # type: ignore

    def test_extra_non_redacted_fields_allowed(self) -> None:
        """Extra fields 不在 redaction list 中的不被拒絕。"""
        record = _make_success_record()
        record["extra_notes"] = "Some notes"
        result = validate_evidence_record(record)
        assert result["extra_notes"] == "Some notes"


# ─────────────────────────────────────────────────────────────────
# validate_adr_evidence_reference tests
# ─────────────────────────────────────────────────────────────────
class TestValidateAdrEvidenceReference:
    """ADR evidence reference projection 驗證。"""

    def test_valid_full_reference(self) -> None:
        ref = {
            "run_id": "run-abc",
            "model_id": "model-xyz",
            "input_fixture_id": "fixture-001",
            "outcome": "success",
            "failure_category": None,
        }
        result = validate_adr_evidence_reference(ref)
        assert result is ref

    def test_valid_partial_reference(self) -> None:
        """只填一部分允許的鍵也合法。"""
        ref = {"run_id": "run-abc", "outcome": "success"}
        result = validate_adr_evidence_reference(ref)
        assert result is ref

    def test_empty_reference_allowed(self) -> None:
        ref: dict = {}
        result = validate_adr_evidence_reference(ref)
        assert result is ref

    def test_disallowed_key_transcript_rejected(self) -> None:
        ref = {
            "run_id": "run-abc",
            "transcript": "Full transcript",
        }
        with pytest.raises(EvidenceValidationError, match="disallowed keys"):
            validate_adr_evidence_reference(ref)

    def test_disallowed_key_model_revision_rejected(self) -> None:
        ref = {
            "run_id": "run-abc",
            "model_revision": "v2.0",
        }
        with pytest.raises(EvidenceValidationError, match="disallowed keys"):
            validate_adr_evidence_reference(ref)

    def test_disallowed_key_audio_duration_rejected(self) -> None:
        ref = {
            "run_id": "run-abc",
            "audio_duration_ms": 5000,
        }
        with pytest.raises(EvidenceValidationError, match="disallowed keys"):
            validate_adr_evidence_reference(ref)

    def test_disallowed_key_language_rejected(self) -> None:
        ref = {
            "run_id": "run-abc",
            "language": "zh",
        }
        with pytest.raises(EvidenceValidationError, match="disallowed keys"):
            validate_adr_evidence_reference(ref)

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="must be a dict"):
            validate_adr_evidence_reference([1, 2, 3])  # type: ignore

    def test_only_allowed_keys_constant(self) -> None:
        """確認 ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS 精確為 5 個。"""
        assert len(ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS) == 5
        assert ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS == frozenset(
            {"run_id", "model_id", "input_fixture_id", "outcome", "failure_category"}
        )
