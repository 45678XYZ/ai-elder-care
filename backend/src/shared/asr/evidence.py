"""
Evidence Validator 與 ADR Evidence-Reference Projection。

驗證結構化證據紀錄的 success/failure 條件、required fields、
redaction 禁止欄位，以及僅允許五個鍵的 ADR evidence-reference projection。

禁止依賴：handlers、HTTP、DB、AWS SDK、Notebook runtime、HF token。
"""
from __future__ import annotations

from typing import Any, FrozenSet


# ─────────────────────────────────────────────────────────────────
# Evidence Required Fields
# ─────────────────────────────────────────────────────────────────
EVIDENCE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "run_id",
        "recorded_at",
        "model_id",
        "model_revision",
        "language",
        "input_format",
        "input_fixture_id",
        "audio_duration_ms",
        "runtime_kind",
        "dependency_manifest_digest",
        "outcome",
        "failure_prerequisite",
        "failure_category",
        "transcript_present",
        "transcript_character_count",
        "evidence_redaction_version",
    }
)

# ─────────────────────────────────────────────────────────────────
# Redaction — 禁止出現在 evidence record 的欄位
# ─────────────────────────────────────────────────────────────────
EVIDENCE_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        "transcript",
        "token",
        "hf_token",
        "audio",
        "audio_bytes",
        "pcm_samples",
        "prompt_id",
        "formo_prompt_id",
        "raw_response",
        "raw_provider_response",
    }
)

# ─────────────────────────────────────────────────────────────────
# ADR Evidence-Reference — 僅允許五個鍵
# ─────────────────────────────────────────────────────────────────
ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "run_id",
        "model_id",
        "input_fixture_id",
        "outcome",
        "failure_category",
    }
)


# ─────────────────────────────────────────────────────────────────
# Validation Error
# ─────────────────────────────────────────────────────────────────
class EvidenceValidationError(Exception):
    """Evidence 驗證失敗。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


# ─────────────────────────────────────────────────────────────────
# validate_evidence_record
# ─────────────────────────────────────────────────────────────────
def validate_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    驗證結構化證據紀錄。

    驗證規則：
    1. 所有 required fields 必須存在。
    2. 禁止出現 redacted fields。
    3. outcome=success → transcript_present=true, transcript_character_count > 0 整數；
       record 不可含 "transcript" 欄位。
    4. outcome=failure → failure_prerequisite 與 failure_category 必須非空白。

    Args:
        record: 證據紀錄 dict。

    Returns:
        通過驗證的 record。

    Raises:
        EvidenceValidationError: 驗證失敗。
    """
    if not isinstance(record, dict):
        raise EvidenceValidationError(
            "Evidence record must be a dict.", field=None
        )

    # ── Step 1: Required fields ──
    missing = EVIDENCE_REQUIRED_FIELDS - set(record.keys())
    if missing:
        raise EvidenceValidationError(
            f"Missing required fields: {sorted(missing)}.",
            field=sorted(missing)[0],
        )

    # ── Step 2: Redacted fields ──
    present_redacted = EVIDENCE_REDACTED_FIELDS & set(record.keys())
    if present_redacted:
        raise EvidenceValidationError(
            f"Record contains redacted/forbidden fields: {sorted(present_redacted)}.",
            field=sorted(present_redacted)[0],
        )

    # ── Step 3 & 4: Outcome-specific validation ──
    outcome = record.get("outcome")

    if outcome == "success":
        # transcript_present must be true
        if record.get("transcript_present") is not True:
            raise EvidenceValidationError(
                "outcome=success requires transcript_present=true.",
                field="transcript_present",
            )
        # transcript_character_count must be > 0 integer
        tcc = record.get("transcript_character_count")
        if not isinstance(tcc, int) or tcc <= 0:
            raise EvidenceValidationError(
                "outcome=success requires transcript_character_count > 0 integer.",
                field="transcript_character_count",
            )
        # record CANNOT contain a "transcript" field (already caught by redaction,
        # but explicit check for clarity)
        if "transcript" in record:
            raise EvidenceValidationError(
                "Evidence record must not contain 'transcript' field.",
                field="transcript",
            )

    elif outcome == "failure":
        # failure_prerequisite must be non-blank
        fp = record.get("failure_prerequisite")
        if not isinstance(fp, str) or not fp.strip():
            raise EvidenceValidationError(
                "outcome=failure requires non-blank failure_prerequisite.",
                field="failure_prerequisite",
            )
        # failure_category must be non-blank
        fc = record.get("failure_category")
        if not isinstance(fc, str) or not fc.strip():
            raise EvidenceValidationError(
                "outcome=failure requires non-blank failure_category.",
                field="failure_category",
            )

    return record


# ─────────────────────────────────────────────────────────────────
# validate_adr_evidence_reference
# ─────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# ADR Template Mandatory Headings
# ─────────────────────────────────────────────────────────────────
ADR_MANDATORY_HEADINGS: tuple[str, ...] = (
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
)


def validate_adr_template_headings(markdown_text: str) -> list[str]:
    """
    驗證 ADR Markdown 範本是否包含所有 mandatory headings。

    檢查邏輯：每個 mandatory heading 必須以 `# heading`、`## heading` 或
    `### heading`（case-insensitive）的形式出現在文件中。

    Args:
        markdown_text: ADR Markdown 範本全文。

    Returns:
        缺少的 heading 名稱列表（空 list 表示通過）。

    Raises:
        EvidenceValidationError: 如果缺少任何 mandatory heading。
    """
    if not isinstance(markdown_text, str):
        raise EvidenceValidationError(
            "ADR template must be a string.", field=None
        )

    # 將所有 markdown heading 解析出來（支援 #, ##, ### 等）
    import re

    heading_pattern = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
    found_headings_raw = heading_pattern.findall(markdown_text)
    # Normalize: lower, strip, replace spaces/hyphens/underscores
    def _normalize(s: str) -> str:
        return re.sub(r"[\s_\-]+", "_", s.strip().lower())

    found_normalized = {_normalize(h) for h in found_headings_raw}

    missing: list[str] = []
    for heading in ADR_MANDATORY_HEADINGS:
        if _normalize(heading) not in found_normalized:
            missing.append(heading)

    if missing:
        raise EvidenceValidationError(
            f"ADR template is missing mandatory headings: {missing}.",
            field="headings",
        )

    return []  # all present


def validate_adr_evidence_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """
    驗證 ADR evidence-reference projection。

    Only allows: run_id, model_id, input_fixture_id, outcome, failure_category。
    任何其他鍵都是驗證失敗。

    Args:
        reference: ADR evidence reference dict。

    Returns:
        通過驗證的 reference。

    Raises:
        EvidenceValidationError: 包含不允許的鍵。
    """
    if not isinstance(reference, dict):
        raise EvidenceValidationError(
            "ADR evidence reference must be a dict.", field=None
        )

    disallowed = set(reference.keys()) - ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS
    if disallowed:
        raise EvidenceValidationError(
            f"ADR evidence reference contains disallowed keys: {sorted(disallowed)}. "
            f"Only {sorted(ADR_EVIDENCE_REFERENCE_ALLOWED_KEYS)} are allowed.",
            field=sorted(disallowed)[0],
        )

    return reference
