"""分類體系載入器測試。

驗證高階類別定義與 API 契約對齊、類別驗證與解析、以及體系可抽換。
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


def test_validate_type(taxonomy):
    assert taxonomy.validate_type("medication") is True
    assert taxonomy.validate_type("safety") is True
    assert taxonomy.validate_type("other") is True
    assert taxonomy.validate_type("unknown") is False


def test_resolve_type_with_valid_label(taxonomy):
    type_id, valid = taxonomy.resolve_type("medication")
    assert type_id == "medication"
    assert valid is True

    type_id, valid = taxonomy.resolve_type("diet")
    assert type_id == "diet"
    assert valid is True


def test_resolve_type_with_invalid_label(taxonomy):
    type_id, valid = taxonomy.resolve_type("unknown_label")
    assert type_id == "other"
    assert valid is False

    type_id, valid = taxonomy.resolve_type("")
    assert type_id == "other"
    assert valid is False


def test_taxonomy_is_swappable(assets_copy):
    types_path = assets_copy / "high_level_types.json"
    types = json.loads(types_path.read_text(encoding="utf-8"))
    types["types"].insert(-1, {"id": "social", "display_name": "社交", "description": "人際互動"})
    types["taxonomy_version"] = "uco-2.0.0-test"
    _write_json(types_path, types)

    swapped = load_taxonomy(assets_copy)
    assert swapped.taxonomy_version == "uco-2.0.0-test"
    assert "social" in swapped.type_ids
    assert load_taxonomy().taxonomy_version == "uco-1.0.0"


def test_missing_asset_is_rejected(assets_copy):
    (assets_copy / "high_level_types.json").unlink()
    with pytest.raises(TaxonomyError, match="資產缺失"):
        load_taxonomy(assets_copy)


def test_missing_taxonomy_version_is_rejected(assets_copy):
    types_path = assets_copy / "high_level_types.json"
    types = json.loads(types_path.read_text(encoding="utf-8"))
    del types["taxonomy_version"]
    _write_json(types_path, types)

    with pytest.raises(TaxonomyError, match="taxonomy_version"):
        load_taxonomy(assets_copy)
