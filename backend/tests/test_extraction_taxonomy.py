"""分類體系載入器測試。

重點在三件事：資產與 API 契約對齊、映射解析（精確／祖先／預設回退）、
以及體系可抽換——換掉資產目錄後行為必須跟著變，而不是被程式內的常數綁死。
"""

import json
import shutil
from typing import get_args

import pytest

from src.extraction.config import TAXONOMY_ASSETS_DIR
from src.extraction.taxonomy import TaxonomyError, load_taxonomy
from src.shared.models import SUMMARY_SECTION_KEYS, EventType


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def assets_copy(tmp_path):
    """複製一份資產供抽換測試，避免動到部署包內的正本。"""
    target = tmp_path / "taxonomy"
    shutil.copytree(TAXONOMY_ASSETS_DIR, target)
    return target


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_default_assets_load(taxonomy):
    """預設資產可載入，版本戳記與節點數符合本體論 metadata。"""
    assert taxonomy.taxonomy_version == "uco-1.0.0"
    assert taxonomy.ontology_version == "1.0.0"
    assert len(taxonomy.nodes) == 49
    assert taxonomy.default_type == "other"


def test_high_level_types_match_api_contract(taxonomy):
    """高階類別資產必須與 docs/api.md 的 EventType 一致，否則會寫出前端不認得的 type。"""
    assert taxonomy.type_ids == get_args(EventType)
    assert taxonomy.type_ids == SUMMARY_SECTION_KEYS


def test_every_leaf_resolves_to_a_type(taxonomy):
    """所有葉節點都必須映射成功；載入器本身也會在漏登記時拋錯。"""
    assert taxonomy.unmapped_leaf_ids() == ()
    for concept_id in taxonomy.leaf_ids():
        assert taxonomy.high_level_type(concept_id) in taxonomy.type_ids


@pytest.mark.parametrize(
    "concept_id,expected",
    [
        ("UCO.BehavioralRecord.NutritionEatingBehavior.MainMeal", "diet"),
        ("UCO.BehavioralRecord.NutritionEatingBehavior.WaterIntake", "diet"),
        ("UCO.BehavioralRecord.PhysicalActivityMobility.WalkingAmbulation", "activity"),
        ("UCO.BehavioralRecord.SleepRestBehavior.SleepOnset", "sleep"),
        ("UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication", "medication"),
        ("UCO.BehavioralRecord.EmotionalMentalState.DepressedAffect", "wellbeing"),
        ("UCO.BehavioralRecord.CognitionOrientation.OrientationImpairment", "wellbeing"),
        # 決策 A-1：生理量測歸 wellbeing（身體症狀與情緒）
        ("UCO.StatusOutcome.PhysiologicalMeasurement.VitalSignRecord", "wellbeing"),
        # 決策 A-2：安全事件獨立為第七類
        ("UCO.StatusOutcome.SafetyIncident.PhysicalFall", "safety"),
        ("UCO.StatusOutcome.SafetyIncident.FraudScamIncident", "safety"),
        # 決策 A-1：人際社交不含身體動作，歸 other
        ("UCO.BehavioralRecord.InterpersonalSocialBehavior.FamilyInterpersonal", "other"),
        ("UCO.BehavioralRecord.InterpersonalSocialBehavior.MediaConsumption", "other"),
        ("UCO.ExtendedDomain.GeneralCommerce", "other"),
    ],
)
def test_decided_mappings(taxonomy, concept_id, expected):
    """鎖定計畫中已定案的映射，避免日後改資產時無聲漂移。"""
    assert taxonomy.high_level_type(concept_id) == expected


def test_leaf_inherits_from_ancestor(taxonomy):
    """葉節點沒有自己的映射，應由 level 2 類別節點繼承。"""
    leaf = "UCO.BehavioralRecord.MedicationBehavior.AdverseDrugEffect"
    assert leaf not in taxonomy.mappings
    event_type, matched = taxonomy.resolve_type(leaf)
    assert event_type == "medication"
    assert matched == "UCO.BehavioralRecord.MedicationBehavior"


def test_ancestors_chain(taxonomy):
    assert taxonomy.ancestors("UCO.BehavioralRecord.SleepRestBehavior.SleepOnset") == (
        "UCO.BehavioralRecord.SleepRestBehavior",
        "UCO.BehavioralRecord",
        "UCO",
    )
    assert taxonomy.ancestors("UCO") == ()
    assert taxonomy.ancestors("UCO.NotExist") == ()


def test_unknown_concept_falls_back_and_warns(taxonomy, caplog):
    """未知節點退回預設類別並告警，不得靜默丟棄事件。"""
    with caplog.at_level("WARNING"):
        assert taxonomy.high_level_type("UCO.BehavioralRecord.BrandNewThing") == "other"
    assert "無法映射" in caplog.text
    assert taxonomy.resolve_type("UCO.BehavioralRecord.BrandNewThing")[1] is None


def test_taxonomy_is_swappable(assets_copy):
    """抽換資產後行為必須跟著改：新增高階類別並改映射。"""
    types_path = assets_copy / "high_level_types.json"
    types = json.loads(types_path.read_text(encoding="utf-8"))
    types["types"].insert(-1, {"id": "social", "display_name": "社交", "description": "人際互動"})
    _write_json(types_path, types)

    map_path = assets_copy / "concept_type_map.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    mapping["mappings"]["UCO.BehavioralRecord.InterpersonalSocialBehavior"] = "social"
    mapping["taxonomy_version"] = "uco-2.0.0-test"
    _write_json(map_path, mapping)

    swapped = load_taxonomy(assets_copy)
    assert swapped.taxonomy_version == "uco-2.0.0-test"
    assert "social" in swapped.type_ids
    assert (
        swapped.high_level_type("UCO.BehavioralRecord.InterpersonalSocialBehavior.FamilyInterpersonal")
        == "social"
    )
    # 正本不受影響，確認快取以目錄為 key
    assert load_taxonomy().taxonomy_version == "uco-1.0.0"


def test_mapping_to_undefined_type_is_rejected(assets_copy):
    map_path = assets_copy / "concept_type_map.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    mapping["mappings"]["UCO.StatusOutcome.SafetyIncident"] = "danger"
    _write_json(map_path, mapping)

    with pytest.raises(TaxonomyError, match="未定義的高階類別"):
        load_taxonomy(assets_copy)


def test_mapping_to_unknown_node_is_rejected(assets_copy):
    map_path = assets_copy / "concept_type_map.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    mapping["mappings"]["UCO.NoSuchCategory"] = "other"
    _write_json(map_path, mapping)

    with pytest.raises(TaxonomyError, match="不存在的節點"):
        load_taxonomy(assets_copy)


def test_unmapped_leaf_is_rejected_at_load(assets_copy):
    """移掉一個類別映射後，其下葉節點會落到預設類別，載入必須直接失敗。"""
    map_path = assets_copy / "concept_type_map.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    del mapping["mappings"]["UCO.BehavioralRecord.SleepRestBehavior"]
    _write_json(map_path, mapping)

    with pytest.raises(TaxonomyError, match="無法映射到高階類別"):
        load_taxonomy(assets_copy)


def test_missing_asset_is_rejected(assets_copy):
    (assets_copy / "high_level_types.json").unlink()
    with pytest.raises(TaxonomyError, match="資產缺失"):
        load_taxonomy(assets_copy)


def test_property_registry_and_synonyms_are_available(taxonomy):
    """後續 Task 需要屬性清冊與同義詞字典，載入器要一併提供。"""
    assert "global_properties" in taxonomy.property_registry
    assert "node_properties" in taxonomy.property_registry
    assert taxonomy.synonym_dictionary
