"""HMLC 剪枝測試。"""

import pytest

from src.extraction.models import LabelHit
from src.extraction.pruner import concept_ids, prune_label_hits
from src.extraction.taxonomy import load_taxonomy

MEDICATION = "UCO.BehavioralRecord.MedicationBehavior"
ADVERSE = "UCO.BehavioralRecord.MedicationBehavior.AdverseDrugEffect"
SCHEDULED = "UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication"
SLEEP = "UCO.BehavioralRecord.SleepRestBehavior"
SLEEP_ONSET = "UCO.BehavioralRecord.SleepRestBehavior.SleepOnset"
FALL = "UCO.StatusOutcome.SafetyIncident.PhysicalFall"


@pytest.fixture
def taxonomy():
    return load_taxonomy()


def hit(concept_id: str, confidence: float = 0.9) -> LabelHit:
    return LabelHit(concept_id=concept_id, confidence=confidence)


def test_leaf_suppresses_ancestors(taxonomy):
    """葉節點命中時壓制祖先，避免同一件事被標成兩層。"""
    result = prune_label_hits([hit(MEDICATION), hit(ADVERSE)], taxonomy)
    assert concept_ids(result) == (ADVERSE,)


def test_parent_fallback_is_retained(taxonomy):
    """類別節點命中但其下無葉節點命中時保留，不因不夠具體而丟事件。"""
    result = prune_label_hits([hit(MEDICATION)], taxonomy)
    assert concept_ids(result) == (MEDICATION,)


def test_sibling_leaves_both_kept(taxonomy):
    """同一父節點下的兩個葉節點各自成立，只壓制父節點。"""
    result = prune_label_hits([hit(MEDICATION), hit(SCHEDULED, 0.8), hit(ADVERSE, 0.7)], taxonomy)
    assert concept_ids(result) == (SCHEDULED, ADVERSE)


def test_suppression_does_not_cross_branches(taxonomy):
    """用藥分支的葉節點不應壓制睡眠分支的父節點。"""
    result = prune_label_hits([hit(ADVERSE, 0.95), hit(SLEEP, 0.6)], taxonomy)
    assert concept_ids(result) == (ADVERSE, SLEEP)


def test_output_is_deterministic_regardless_of_input_order(taxonomy):
    """冪等性依賴確定性輸出：同一組命中不論輸入順序都要得到同一結果。"""
    first = prune_label_hits([hit(SLEEP_ONSET, 0.7), hit(FALL, 0.9)], taxonomy)
    second = prune_label_hits([hit(FALL, 0.9), hit(SLEEP_ONSET, 0.7)], taxonomy)
    assert concept_ids(first) == concept_ids(second) == (FALL, SLEEP_ONSET)


def test_duplicate_hits_keep_highest_confidence(taxonomy):
    result = prune_label_hits([hit(FALL, 0.4), hit(FALL, 0.85)], taxonomy)
    assert len(result) == 1
    assert result[0].confidence == pytest.approx(0.85)


def test_unknown_concept_is_dropped_with_warning(taxonomy, caplog):
    with caplog.at_level("WARNING"):
        result = prune_label_hits([hit("UCO.BehavioralRecord.MadeUpThing"), hit(FALL)], taxonomy)
    assert concept_ids(result) == (FALL,)
    assert "未知節點" in caplog.text


def test_shallow_levels_are_dropped(taxonomy, caplog):
    """根節點與領域節點粗到無法映射高階類別，必須丟棄並告警。"""
    with caplog.at_level("WARNING"):
        result = prune_label_hits([hit("UCO"), hit("UCO.BehavioralRecord"), hit(FALL)], taxonomy)
    assert concept_ids(result) == (FALL,)
    assert "層級過淺" in caplog.text


def test_min_confidence_filter(taxonomy):
    result = prune_label_hits([hit(FALL, 0.2), hit(SLEEP_ONSET, 0.8)], taxonomy, min_confidence=0.5)
    assert concept_ids(result) == (SLEEP_ONSET,)


def test_empty_input(taxonomy):
    assert prune_label_hits([], taxonomy) == ()
