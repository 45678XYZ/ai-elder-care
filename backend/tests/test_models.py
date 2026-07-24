"""backend/src/shared/models.py Pydantic Models 單元測試。"""

import pytest
from pydantic import ValidationError

from src.shared.models import (
    ElderCreate,
    ElderResponse,
    ElderUpdate,
    FamilyMember,
)


def test_family_member_model():
    """測試 FamilyMember 模型欄位與序列化。"""
    fm = FamilyMember(relation="兒子", name="陳志明", note="在台北工作")
    assert fm.relation == "兒子"
    assert fm.name == "陳志明"
    assert fm.note == "在台北工作"

    data = fm.model_dump()
    assert data == {"relation": "兒子", "name": "陳志明", "note": "在台北工作"}


def test_elder_create_model_defaults_and_validation():
    """測試 ElderCreate 模型的必填欄位與預設值帶入。"""
    # 僅填必填欄位 name
    ec = ElderCreate(name="陳阿蘭")
    assert ec.name == "陳阿蘭"
    assert ec.lang_preference == "zh-TW"
    assert ec.address_region is None
    assert ec.health_notes == []
    assert ec.family == []
    assert ec.caregiver_ids == []

    # 完整填寫
    ec_full = ElderCreate(
        name="陳阿蘭",
        nickname="阿蘭嬤",
        birth_year=1948,
        gender="female",
        lang_preference="hak",
        address_region="台北市大安區",
        health_notes=["高血壓"],
        family=[FamilyMember(relation="兒子", name="志明")],
        habit_note="早起散步",
        caregiver_ids=["cg_001"],
    )
    assert ec_full.lang_preference == "hak"
    assert ec_full.address_region == "台北市大安區"
    assert len(ec_full.family) == 1
    assert ec_full.family[0].name == "志明"

    # 缺少必填欄位 name 應丟出 ValidationError
    with pytest.raises(ValidationError):
        ElderCreate()


def test_elder_update_model():
    """測試 ElderUpdate 模型（所有欄位皆選填）。"""
    eu = ElderUpdate(nickname="蘭姊", address_region="新北市板橋區")
    assert eu.name is None
    assert eu.nickname == "蘭姊"
    assert eu.address_region == "新北市板橋區"

    # dump exclude_unset 時只保留更新欄位
    update_dict = eu.model_dump(exclude_unset=True)
    assert update_dict == {"nickname": "蘭姊", "address_region": "新北市板橋區"}


def test_elder_response_model():
    """測試 ElderResponse 完整結構與序列化。"""
    er = ElderResponse(
        elder_id="eld_abc123",
        name="王大同",
        created_at="2026-07-24T15:00:00+08:00",
        updated_at="2026-07-24T15:00:00+08:00",
    )
    assert er.elder_id == "eld_abc123"
    assert er.created_at == "2026-07-24T15:00:00+08:00"
    assert er.health_notes == []
