"""
Tests for ADR Template Validator — evidence.py validate_adr_template_headings。

驗證：
- 完整 ADR template（所有 13 個 mandatory headings）通過
- 缺少任一 heading → EvidenceValidationError
- heading 允許不同層級（#, ##, ###）
- heading 匹配為 case-insensitive 且支援 underscore/hyphen/space 互換
- 非字串輸入 → EvidenceValidationError
- ADR_MANDATORY_HEADINGS 常量精確為 13 項
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.shared.asr.evidence import (
    ADR_MANDATORY_HEADINGS,
    EvidenceValidationError,
    validate_adr_template_headings,
)


# ─────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────
def _make_complete_adr_template() -> str:
    """建立包含所有 mandatory headings 的完整 ADR template。"""
    sections = [
        "# Title",
        "## Status",
        "## Date",
        "## Owners",
        "## Scope",
        "## Candidate Models",
        "## Evidence References",
        "## AWS Capability Gate Status",
        "## Decision",
        "## Rationale",
        "## Risks",
        "## Non Goals",
        "## Follow Up Actions",
    ]
    return "\n\n".join(sections) + "\n"


def _make_template_missing_heading(heading_to_remove: str) -> str:
    """建立缺少一個 heading 的 ADR template。"""
    all_headings = [
        ("title", "# Title"),
        ("status", "## Status"),
        ("date", "## Date"),
        ("owners", "## Owners"),
        ("scope", "## Scope"),
        ("candidate_models", "## Candidate Models"),
        ("evidence_references", "## Evidence References"),
        ("aws_capability_gate_status", "## AWS Capability Gate Status"),
        ("decision", "## Decision"),
        ("rationale", "## Rationale"),
        ("risks", "## Risks"),
        ("non_goals", "## Non Goals"),
        ("follow_up_actions", "## Follow Up Actions"),
    ]
    sections = [
        md for key, md in all_headings if key != heading_to_remove
    ]
    return "\n\n".join(sections) + "\n"


# ─────────────────────────────────────────────────────────────────
# Tests: Complete template passes
# ─────────────────────────────────────────────────────────────────
class TestAdrTemplateComplete:
    """完整 ADR template 通過驗證。"""

    def test_all_headings_present_passes(self) -> None:
        template = _make_complete_adr_template()
        result = validate_adr_template_headings(template)
        assert result == []

    def test_headings_with_content_passes(self) -> None:
        """Headings with surrounding content still pass."""
        template = (
            "# Title\n\nASR Model Validation\n\n"
            "## Status\n\nAccepted\n\n"
            "## Date\n\n2024-01-15\n\n"
            "## Owners\n\nTeam A\n\n"
            "## Scope\n\nASR model evaluation\n\n"
            "## Candidate Models\n\n- Model A\n- Model B\n\n"
            "## Evidence References\n\n| run_id | outcome |\n\n"
            "## AWS Capability Gate Status\n\nIncomplete\n\n"
            "## Decision\n\nDefer\n\n"
            "## Rationale\n\nNot ready\n\n"
            "## Risks\n\n- Risk 1\n\n"
            "## Non Goals\n\n- Production invocation\n\n"
            "## Follow Up Actions\n\n- Complete gate\n"
        )
        result = validate_adr_template_headings(template)
        assert result == []

    @pytest.mark.parametrize(
        "relative_path",
        [
            "docs/adr/asr-ce-production-approval.md",
            "docs/adr/asr-formo-production-approval.md",
        ],
    )
    def test_model_approval_adrs_exist_and_are_complete(
        self, relative_path: str
    ) -> None:
        """個別模型 ADR 必須存在、完整，且目前明確標示未核准。"""
        repo_root = Path(__file__).resolve().parents[3]
        adr_path = repo_root / relative_path
        content = adr_path.read_text(encoding="utf-8")

        assert validate_adr_template_headings(content) == []
        assert "未核准" in content


# ─────────────────────────────────────────────────────────────────
# Tests: Missing heading raises
# ─────────────────────────────────────────────────────────────────
class TestAdrTemplateMissingHeading:
    """缺少任一 mandatory heading → EvidenceValidationError。"""

    @pytest.mark.parametrize(
        "missing",
        [
            "title",
            "status",
            "date",
            "owners",
            "scope",
            "candidate_models",
            "evidence_references",
            "aws_capability_gate_status",
            "decision",
            "rationale",
            "risks",
            "non_goals",
            "follow_up_actions",
        ],
    )
    def test_missing_single_heading_raises(self, missing: str) -> None:
        template = _make_template_missing_heading(missing)
        with pytest.raises(EvidenceValidationError, match="missing mandatory headings"):
            validate_adr_template_headings(template)

    def test_empty_template_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="missing mandatory headings"):
            validate_adr_template_headings("")


# ─────────────────────────────────────────────────────────────────
# Tests: Heading levels accepted
# ─────────────────────────────────────────────────────────────────
class TestAdrTemplateHeadingLevels:
    """Different heading levels (#, ##, ###) are accepted."""

    def test_level_one_headings_accepted(self) -> None:
        template = "\n".join(
            [
                "# Title",
                "# Status",
                "# Date",
                "# Owners",
                "# Scope",
                "# Candidate Models",
                "# Evidence References",
                "# AWS Capability Gate Status",
                "# Decision",
                "# Rationale",
                "# Risks",
                "# Non Goals",
                "# Follow Up Actions",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []

    def test_level_three_headings_accepted(self) -> None:
        template = "\n".join(
            [
                "### Title",
                "### Status",
                "### Date",
                "### Owners",
                "### Scope",
                "### Candidate Models",
                "### Evidence References",
                "### AWS Capability Gate Status",
                "### Decision",
                "### Rationale",
                "### Risks",
                "### Non Goals",
                "### Follow Up Actions",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []

    def test_mixed_levels_accepted(self) -> None:
        template = "\n".join(
            [
                "# Title",
                "## Status",
                "### Date",
                "## Owners",
                "# Scope",
                "## Candidate Models",
                "### Evidence References",
                "## AWS Capability Gate Status",
                "## Decision",
                "# Rationale",
                "## Risks",
                "### Non Goals",
                "## Follow Up Actions",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []


# ─────────────────────────────────────────────────────────────────
# Tests: Case-insensitive and separator normalization
# ─────────────────────────────────────────────────────────────────
class TestAdrTemplateNormalization:
    """Heading matching is case-insensitive, underscore/hyphen/space interchangeable。"""

    def test_uppercase_headings_pass(self) -> None:
        template = "\n".join(
            [
                "# TITLE",
                "## STATUS",
                "## DATE",
                "## OWNERS",
                "## SCOPE",
                "## CANDIDATE MODELS",
                "## EVIDENCE REFERENCES",
                "## AWS CAPABILITY GATE STATUS",
                "## DECISION",
                "## RATIONALE",
                "## RISKS",
                "## NON GOALS",
                "## FOLLOW UP ACTIONS",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []

    def test_hyphenated_headings_pass(self) -> None:
        template = "\n".join(
            [
                "# Title",
                "## Status",
                "## Date",
                "## Owners",
                "## Scope",
                "## Candidate-Models",
                "## Evidence-References",
                "## AWS-Capability-Gate-Status",
                "## Decision",
                "## Rationale",
                "## Risks",
                "## Non-Goals",
                "## Follow-Up-Actions",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []

    def test_underscore_headings_pass(self) -> None:
        template = "\n".join(
            [
                "# title",
                "## status",
                "## date",
                "## owners",
                "## scope",
                "## candidate_models",
                "## evidence_references",
                "## aws_capability_gate_status",
                "## decision",
                "## rationale",
                "## risks",
                "## non_goals",
                "## follow_up_actions",
            ]
        )
        result = validate_adr_template_headings(template)
        assert result == []


# ─────────────────────────────────────────────────────────────────
# Tests: Invalid input
# ─────────────────────────────────────────────────────────────────
class TestAdrTemplateInvalidInput:
    """Non-string input → EvidenceValidationError。"""

    def test_none_input_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="must be a string"):
            validate_adr_template_headings(None)  # type: ignore

    def test_int_input_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="must be a string"):
            validate_adr_template_headings(42)  # type: ignore

    def test_list_input_raises(self) -> None:
        with pytest.raises(EvidenceValidationError, match="must be a string"):
            validate_adr_template_headings(["# Title"])  # type: ignore


# ─────────────────────────────────────────────────────────────────
# Tests: ADR_MANDATORY_HEADINGS constant
# ─────────────────────────────────────────────────────────────────
class TestAdrMandatoryHeadingsConstant:
    """ADR_MANDATORY_HEADINGS 精確為 13 項。"""

    def test_exactly_13_headings(self) -> None:
        assert len(ADR_MANDATORY_HEADINGS) == 13

    def test_contains_all_expected_headings(self) -> None:
        expected = {
            "title",
            "status",
            "date",
            "owners",
            "scope",
            "candidate_models",
            "evidence_references",
            "aws_capability_gate_status",
            "decision",
            "rationale",
            "risks",
            "non_goals",
            "follow_up_actions",
        }
        assert set(ADR_MANDATORY_HEADINGS) == expected
