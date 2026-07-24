"""backend/src/shared/models.py Pydantic Models 單元測試。"""

import pytest
from pydantic import ValidationError

from src.shared.models import (
    ConversationCreate,
    ConversationResponse,
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


def test_conversation_create_model():
    """測試 ConversationCreate 模型長者發話 vs 系統主動提醒模式、雙語音與三階段時間戳記。"""
    # 模式 1: 長者主動發話
    cc_elder = ConversationCreate(
        elder_id="eld_001",
        elder_transcript="我吃過血壓藥了",
        ai_respond_text="真棒！我幫你記下來了。",
        elder_audio_s3_key="audio/eld_001/input_001.m4a",
        ai_respond_audio_url="https://s3.amazonaws.com/reply.mp3",
        elder_received_at="2026-07-24T17:30:00+08:00",
        ai_responded_at="2026-07-24T17:30:01+08:00",
    )
    assert cc_elder.source == "elder_initiated"
    assert cc_elder.user_status == "replied"
    assert cc_elder.system_status == "success"
    assert cc_elder.ai_prompt_text is None
    assert cc_elder.elder_transcript == "我吃過血壓藥了"
    assert cc_elder.ai_prompt_audio_url is None
    assert cc_elder.elder_audio_s3_key == "audio/eld_001/input_001.m4a"
    assert cc_elder.ai_respond_audio_url == "https://s3.amazonaws.com/reply.mp3"
    assert cc_elder.elder_received_at == "2026-07-24T17:30:00+08:00"
    assert cc_elder.ai_responded_at == "2026-07-24T17:30:01+08:00"

    # 模式 2: 系統主動提醒（長者逾時無回應）
    cc_sys_no_resp = ConversationCreate(
        elder_id="eld_001",
        source="system_routine_inquiry",
        user_status="no_response",
        routine_id="rtn_001",
        ai_prompt_text="阿蘭嬤，吃血壓藥時間到了喔！",
        ai_prompt_audio_url="https://s3.amazonaws.com/prompt_rtn001.mp3",
        prompt_sent_at="2026-07-24T17:00:00+08:00",
    )
    assert cc_sys_no_resp.source == "system_routine_inquiry"
    assert cc_sys_no_resp.user_status == "no_response"
    assert cc_sys_no_resp.elder_transcript is None
    assert cc_sys_no_resp.ai_prompt_text == "阿蘭嬤，吃血壓藥時間到了喔！"
    assert cc_sys_no_resp.ai_prompt_audio_url == "https://s3.amazonaws.com/prompt_rtn001.mp3"
    assert cc_sys_no_resp.prompt_sent_at == "2026-07-24T17:00:00+08:00"

    # 模式 3: 系統處理失敗紀錄
    cc_failed = ConversationCreate(
        elder_id="eld_001",
        system_status="failed",
        error_message="Polly TTS synthesis timeout",
    )
    assert cc_failed.system_status == "failed"
    assert cc_failed.error_message == "Polly TTS synthesis timeout"


def test_conversation_response_model():
    """測試 ConversationResponse 完整結構。"""
    cr = ConversationResponse(
        conversation_id="cnv_999",
        elder_id="eld_001",
        created_at="2026-07-24T17:30:00+08:00",
        elder_transcript="昨天睡得很好",
        ai_respond_text="太棒了！保持規律作息喔。",
        ai_respond_audio_url="https://s3.amazonaws.com/response.mp3",
    )
    assert cr.conversation_id == "cnv_999"
    assert cr.elder_id == "eld_001"
    assert cr.created_at == "2026-07-24T17:30:00+08:00"
    assert cr.ai_respond_audio_url == "https://s3.amazonaws.com/response.mp3"
