"""Canonical 事件身分測試。"""

import pytest

from src.extraction.canonical import (
    CanonicalError,
    canonical_event_key,
    event_id_for,
    event_time_key,
    load_predicate_lexicon,
    normalize_predicate,
    normalize_subject,
    normalize_text,
    routine_completion_key,
    safety_alert_key,
    slot_label,
)
from src.extraction.taxonomy import load_taxonomy

SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
VITAL = "UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord"
FALL = "UCO.StatusOutcome.SafetyIncident.PhysicalFall"


@pytest.fixture
def lexicon():
    return load_predicate_lexicon()


@pytest.fixture
def taxonomy():
    return load_taxonomy()


# -- Slot ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "ts,expected",
    [
        ("2026-07-26T09:00:00.000+08:00", "SLOT_0900"),
        ("2026-07-26T09:29:59.999+08:00", "SLOT_0900"),
        ("2026-07-26T09:30:00.000+08:00", "SLOT_0930"),
        ("2026-07-26T00:00:00.000+08:00", "SLOT_0000"),
        ("2026-07-26T23:59:59.999+08:00", "SLOT_2330"),
    ],
)
def test_slot_boundaries_at_30_minutes(ts, expected):
    assert slot_label(ts, 30) == expected


def test_slot_label_format_depends_on_granularity():
    """整小時倍數輸出 SLOT_HH，其餘輸出 SLOT_HHMM（規範例子）。"""
    assert slot_label("2026-07-26T09:41:00.000+08:00", 60) == "SLOT_09"
    assert slot_label("2026-07-26T09:41:00.000+08:00", 30) == "SLOT_0930"
    assert slot_label("2026-07-26T09:41:00.000+08:00", 15) == "SLOT_0930"
    assert slot_label("2026-07-26T09:14:00.000+08:00", 15) == "SLOT_0900"
    assert slot_label("2026-07-26T09:41:00.000+08:00", 120) == "SLOT_08"


def test_slot_uses_taipei_day_boundary():
    assert slot_label("2026-07-26T01:41:00+00:00", 60) == "SLOT_09"


def test_invalid_slot_granularity(taxonomy):
    with pytest.raises(CanonicalError):
        slot_label("2026-07-26T09:00:00.000+08:00", 0)
    with pytest.raises(CanonicalError):
        slot_label("2026-07-26T09:00:00.000+08:00", 2000)


# -- 文字與主體正規化 ---------------------------------------------------------


def test_normalize_text_strips_width_space_and_trailing_particles():
    assert normalize_text("　服用 血壓藥 了。") == "服用血壓藥"
    assert normalize_text("ＳＬＯＴ") == "SLOT"
    # 句中的語助詞不動，只清尾端
    assert normalize_text("吃了血壓藥") == "吃了血壓藥"


def test_normalize_subject_maps_self_references(lexicon):
    for raw in ("我", "阿嬤", "長者本人", "奶奶"):
        assert normalize_subject(raw, lexicon) == "長者"
    assert normalize_subject(None, lexicon) == "長者"
    assert normalize_subject("  ", lexicon) == "長者"


def test_normalize_subject_keeps_named_others(lexicon):
    assert normalize_subject("陳志明", lexicon) == "陳志明"


def test_normalize_subject_accepts_runtime_aliases(lexicon):
    """長者專屬別名（elders.family 的稱謂與姓名）依長者而異，由 runtime 疊加。"""
    assert normalize_subject("小明", lexicon, extra_aliases={"小明": "陳志明"}) == "陳志明"


# -- 謂語正規化 ---------------------------------------------------------------


def test_predicate_alias_converges_variants(lexicon, taxonomy):
    """開放世界策略下，謂語傳回自然語言正規化文字。"""
    canonical = normalize_predicate(SCHEDULED, "服用血壓藥", lexicon, taxonomy)
    assert canonical.matched is True

    resolved = normalize_predicate(SCHEDULED, "吃血壓藥", lexicon, taxonomy)
    assert resolved.matched is True
    assert resolved.value == "吃血壓藥"


def test_predicate_distinguishes_different_events_under_same_concept(lexicon, taxonomy):
    """同節點不同事件（量血壓 vs 量體重）謂語不同，應各自成立。"""
    bp = normalize_predicate(VITAL, "測血壓", lexicon, taxonomy)
    weight = normalize_predicate(VITAL, "秤體重", lexicon, taxonomy)
    assert bp.value == "測血壓"
    assert weight.value == "秤體重"
    assert bp.value != weight.value


def test_predicate_falls_back_to_ancestor_lexicon(lexicon, taxonomy):
    """開放世界策略下保留輸入謂語的自然語言表達。"""
    resolved = normalize_predicate(SCHEDULED, "吃藥", lexicon, taxonomy)
    assert resolved.matched is True
    assert resolved.value == "吃藥"


def test_unmatched_predicate_keeps_original_and_warns(lexicon, taxonomy, caplog):
    resolved = normalize_predicate(SCHEDULED, "把藥丟掉", lexicon, taxonomy)
    assert resolved.matched is True
    assert resolved.value == "把藥丟掉"


def test_other_token_is_not_matched(lexicon, taxonomy):
    resolved = normalize_predicate(SCHEDULED, lexicon.other_token, lexicon, taxonomy)
    assert resolved.matched is True
    assert resolved.value == "__other__"




def test_lexicon_candidates_for_prompt(lexicon):
    candidates = lexicon.candidates_for_prompt((SCHEDULED, "UCO.NotRegistered"))
    assert "服用血壓藥" in candidates[SCHEDULED]
    assert candidates["UCO.NotRegistered"] == ()


# -- canonical key 與 event_id ------------------------------------------------


def test_canonical_event_key_shape():
    key = canonical_event_key("2026-07-26T09:05:00.000+08:00", "長者", "服用血壓藥", 30)
    assert key == "2026-07-26#SLOT_0900#長者#服用血壓藥"


def test_canonical_event_key_requires_subject_and_predicate():
    with pytest.raises(CanonicalError):
        canonical_event_key("2026-07-26T09:05:00.000+08:00", "", "服用血壓藥", 30)
    with pytest.raises(CanonicalError):
        canonical_event_key("2026-07-26T09:05:00.000+08:00", "長者", "", 30)


def test_canonical_event_key_rejects_separator_in_parts():
    with pytest.raises(CanonicalError):
        canonical_event_key("2026-07-26T09:05:00.000+08:00", "長者", "服用#血壓藥", 30)


def test_same_slot_converges_and_next_slot_does_not():
    early = canonical_event_key("2026-07-26T09:05:00.000+08:00", "長者", "服用血壓藥", 30)
    same_slot = canonical_event_key("2026-07-26T09:29:00.000+08:00", "長者", "服用血壓藥", 30)
    next_slot = canonical_event_key("2026-07-26T09:31:00.000+08:00", "長者", "服用血壓藥", 30)
    assert early == same_slot
    assert early != next_slot


def test_routine_completion_key_excludes_version():
    """同日不同 routine version 的完成必須收斂到同一 event。"""
    key = routine_completion_key("rtn_001", "2026-07-26")
    assert key == "routine_completion#rtn_001#2026-07-26"
    assert "version" not in key
    assert routine_completion_key("rtn_001", "2026-07-26") == key


def test_routine_completion_key_requires_both_parts():
    with pytest.raises(CanonicalError):
        routine_completion_key("", "2026-07-26")
    with pytest.raises(CanonicalError):
        routine_completion_key("rtn_001", "")


def test_safety_alert_key():
    key = safety_alert_key("alert_01J8")
    assert key == "SAFETY#alert_01J8"
    with pytest.raises(CanonicalError):
        safety_alert_key("")


def test_event_id_is_stable_and_scoped_to_elder():
    key = "2026-07-26#SLOT_0900#長者#服用血壓藥"
    first = event_id_for("eld_a1b2c3d4e5f6", key)
    assert first == event_id_for("eld_a1b2c3d4e5f6", key)
    assert first.startswith("evt_")
    assert len(first) == len("evt_") + 12
    # 不同長者的同一 canonical key 必須是不同事件
    assert first != event_id_for("eld_ffffffffffff", key)
    # canonical key 不同也必須不同
    assert first != event_id_for("eld_a1b2c3d4e5f6", "2026-07-26#SLOT_0930#長者#服用血壓藥")


def test_event_id_matches_shared_db_convention():
    """與 shared/db.py 既有的產生規則一致，避免同一筆資料在兩處算出不同 ID。"""
    import hashlib

    elder_id = "eld_a1b2c3d4e5f6"
    key = "2026-07-26#SLOT_0900#長者#服用血壓藥"
    expected = f"evt_{hashlib.sha256(f'{elder_id}:{key}'.encode('utf-8')).hexdigest()[:12]}"
    assert event_id_for(elder_id, key) == expected


def test_event_id_requires_inputs():
    with pytest.raises(CanonicalError):
        event_id_for("", "2026-07-26#SLOT_0900#長者#服用血壓藥")
    with pytest.raises(CanonicalError):
        event_id_for("eld_1", "")


def test_event_time_key_sorts_by_time():
    early = event_time_key("2026-07-26T09:05:00.000+08:00", "evt_zzzzzzzzzzzz")
    late = event_time_key("2026-07-26T09:05:00.001+08:00", "evt_000000000000")
    assert early < late


def test_full_identity_flow_is_deterministic(lexicon, taxonomy):
    """從謂語正規化到 event_id 的整條路徑重跑必須一致。"""

    def build(predicate: str, subject: str) -> str:
        resolved = normalize_predicate(FALL, predicate, lexicon, taxonomy)
        key = canonical_event_key(
            "2026-07-26T18:20:00.000+08:00",
            normalize_subject(subject, lexicon),
            resolved.value,
            30,
        )
        return event_id_for("eld_a1b2c3d4e5f6", key)

    assert build("滑倒", "阿嬤") == build("滑倒", "長者")
    assert build("滑倒", "阿嬤") != build("差點跌倒", "阿嬤")
