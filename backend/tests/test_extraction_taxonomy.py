"""分類體系載入器測試。

驗證高階類別定義與 API 契約對齊、pseudo concept 解析、以及體系可抽換。
"""

import json
import shutil
from typing import get_args

import pytest

from src.extraction.pipeline import TAXONOMY_ASSETS_DIR
from src.extraction.taxonomy import TaxonomyError, load_taxonomy
from src.shared.models import SUMMARY_SECTION_KEYS, EventType


@pytest.fixture
def taxonomy():
    return load_taxonomy()


@pytest.fixture
def assets_copy(tmp_path):
    target = tmp_path / "taxonomy"
    shutil.copytree(TAXONOMY_ASSETS_DIR, target)
    return target


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_default_assets_load(taxonomy):
    assert taxonomy.taxonomy_version == "uco-1.0.0"
    assert taxonomy.default_type == "other"


def test_high_level_types_match_api_contract(taxonomy):
    assert taxonomy.type_ids == get_args(EventType)
    assert taxonomy.type_ids == SUMMARY_SECTION_KEYS


def test_pseudo_concept_resolves_to_type(taxonomy):
    assert taxonomy.high_level_type("UCO.HighLevel.medication") == "medication"
    assert taxonomy.high_level_type("UCO.HighLevel.safety") == "safety"
    assert taxonomy.high_level_type("UCO.HighLevel.other") == "other"


def test_pseudo_concept_is_recognized(taxonomy):
    assert taxonomy.is_pseudo_concept("UCO.HighLevel.diet") is True
    assert taxonomy.is_pseudo_concept("UCO.HighLevel.unknown") is False
    assert taxonomy.is_pseudo_concept("UCO.BehavioralRecord") is False


def test_pseudo_concept_for_label(taxonomy):
    concept_id, valid = taxonomy.pseudo_concept_for_label("medication")
    assert concept_id == "UCO.HighLevel.medication"
    assert valid is True

    concept_id, valid = taxonomy.pseudo_concept_for_label("unknown_label")
    assert concept_id == "UCO.HighLevel.other"
    assert valid is False


def test_unknown_concept_falls_back_to_default(taxonomy):
    assert taxonomy.high_level_type("UCO.SomethingElse") == "other"


def test_get_returns_none_for_unknown(taxonomy):
    assert taxonomy.get("UCO.NotExist") is None


def test_get_returns_dict_for_pseudo_concept(taxonomy):
    result = taxonomy.get("UCO.HighLevel.diet")
    assert result is not None
    assert result["concept_id"] == "UCO.HighLevel.diet"


def test_taxonomy_is_swappable(assets_copy):
    types_path = assets_copy / "high_level_types.json"
    types = json.loads(types_path.read_text(encoding="utf-8"))
    types["types"].insert(-1, {"id": "social", "display_name": "社交", "description": "人際互動"})
    _write_json(types_path, types)

    map_path = assets_copy / "concept_type_map.json"
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    mapping["taxonomy_version"] = "uco-2.0.0-test"
    _write_json(map_path, mapping)

    swapped = load_taxonomy(assets_copy)
    assert swapped.taxonomy_version == "uco-2.0.0-test"
    assert "social" in swapped.type_ids
    assert load_taxonomy().taxonomy_version == "uco-1.0.0"


def test_missing_asset_is_rejected(assets_copy):
    (assets_copy / "high_level_types.json").unlink()
    with pytest.raises(TaxonomyError, match="資產缺失"):
        load_taxonomy(assets_copy)
