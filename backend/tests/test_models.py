"""backend/src/shared/models.py Pydantic Models 單元測試。"""

from typing import get_args

import pytest
from pydantic import ValidationError

from src.shared.models import (
    SUMMARY_SECTION_KEYS,
    ConversationCreate,
    DailySummaryCreate,
    DailySummaryResponse,
    ElderCreate,
    ElderResponse,
    ElderUpdate,
    EventCreate,
    EventResponse,
    EventType,
    FamilyMember,
    RoutineComplete,
    RoutineCreate,
    RoutineDefinition,
    RoutineOccurrence,
    RoutineSchedule,
    RoutineUpdate,
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
    assert ec.hakka_dialect == "htia_sixian"
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
        hakka_dialect="htia_hailu",
        address_region="台北市大安區",
        health_notes=["高血壓"],
        family=[FamilyMember(relation="兒子", name="志明")],
        habit_note="早起散步",
        caregiver_ids=["cg_001"],
    )
    assert ec_full.lang_preference == "hak"
    assert ec_full.hakka_dialect == "htia_hailu"
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
    """測試 ConversationCreate 模型長者發話單一模式與核心欄位。"""
    cc_elder = ConversationCreate(
        elder_id="eld_001",
        session_id="ses_01J8",
        elder_transcript="我吃過血壓藥了",
        ai_respond_text="真棒！我幫你記下來了。",
        ai_respond_audio_s3_key="tts/cnv_001.mp3",
        elder_received_at="2026-07-24T17:30:00+08:00",
        ai_responded_at="2026-07-24T17:30:01+08:00",
    )
    assert cc_elder.session_id == "ses_01J8"
    assert cc_elder.elder_transcript == "我吃過血壓藥了"
    assert cc_elder.ai_respond_text == "真棒！我幫你記下來了。"
    assert cc_elder.ai_respond_audio_s3_key == "tts/cnv_001.mp3"
    assert cc_elder.elder_received_at == "2026-07-24T17:30:00+08:00"
    assert cc_elder.ai_responded_at == "2026-07-24T17:30:01+08:00"

def test_event_models():

    """測試 EventCreate 與 EventResponse 模型。"""
    ec = EventCreate(
        elder_id="eld_001",
        ts="2026-07-25T09:05:00+08:00",
        type="medication",
        detail="已服用血壓藥一顆",
        structured_detail={"medication_name": "血壓藥", "dosage": "1 顆"},
        canonical_event_key="2026-07-25#SLOT_0900#長者#服用血壓藥",
    )
    assert ec.elder_id == "eld_001"
    assert ec.type == "medication"
    assert ec.structured_detail["medication_name"] == "血壓藥"
    assert ec.canonical_event_key == "2026-07-25#SLOT_0900#長者#服用血壓藥"

    er = EventResponse(
        event_id="evt_123",
        elder_id="eld_001",
        ts="2026-07-25T09:05:00+08:00",
        type="medication",
        detail="已服用血壓藥一顆",
    )
    assert er.event_id == "evt_123"
    assert er.type == "medication"


def test_event_create_accepts_safety_type_and_taxonomy_fields():
    """safety 為第七個高階類別；concept_id／taxonomy_version 為後端內部欄位。"""
    ec = EventCreate(
        elder_id="eld_001",
        ts="2026-07-25T18:20:00.000+08:00",
        type="safety",
        concept_id="UCO.StatusOutcome.SafetyIncident.FallEvent",
        taxonomy_version="uco-1.0.0",
        detail="在浴室滑倒，沒有受傷",
        canonical_event_key="2026-07-25#SLOT_1800#長者#浴室滑倒",
    )
    assert ec.type == "safety"
    assert ec.concept_id == "UCO.StatusOutcome.SafetyIncident.FallEvent"
    assert ec.taxonomy_version == "uco-1.0.0"

    # concept_id／taxonomy_version 不屬於對外回應欄位
    assert "concept_id" not in EventResponse.model_fields
    assert "taxonomy_version" not in EventResponse.model_fields


def test_event_create_rejects_unknown_type():
    """未在高階類別內的值必須被擋下，避免繞過分類映射直接寫入。"""
    with pytest.raises(ValidationError):
        EventCreate(
            elder_id="eld_001",
            ts="2026-07-25T09:05:00+08:00",
            type="fall",
            detail="跌倒",
        )


def test_summary_section_keys_match_event_type():
    """sections 的 key 集合必須等於 EventType，避免兩邊走鐘。"""
    assert SUMMARY_SECTION_KEYS == get_args(EventType)
    assert SUMMARY_SECTION_KEYS[-1] == "other"


def test_daily_summary_model():
    """測試 DailySummaryResponse 模型。"""
    ds = DailySummaryResponse(
        elder_id="eld_001",
        date="2026-07-25",
        overview="今日狀態良好，有按時用藥。",
        sections={key: None for key in SUMMARY_SECTION_KEYS}
        | {"medication": "已服用血壓藥", "diet": "三餐正常"},
        routines={"completed": 1, "missed": 0, "items": []},
        alerts=[],
        generated_at="2026-07-25T20:00:00+08:00",
    )
    assert ds.elder_id == "eld_001"
    assert ds.data_status == "complete"
    assert ds.sections["medication"] == "已服用血壓藥"
    # sections 與 EventType 一一對應，新增高階類別時此斷言會擋住漏改的 section
    assert set(ds.sections) == set(get_args(EventType))
    assert "safety" in ds.sections


def test_routine_models():
    """測試 RoutineSchedule, RoutineCreate, RoutineUpdate 與兩種 Response 模型。"""
    sched = RoutineSchedule(freq="daily", time="09:00")
    rc = RoutineCreate(
        client_request_id="uuid_123",
        elder_id="eld_001",
        title="吃血壓藥",
        type="medication",
        schedule=sched,
    )
    assert rc.title == "吃血壓藥"
    assert rc.schedule.freq == "daily"

    ru = RoutineUpdate(client_request_id="uuid_124", active=False)
    assert ru.active is False
    assert ru.title is None

    rd = RoutineDefinition(
        routine_id="rtn_001",
        elder_id="eld_001",
        title="吃血壓藥",
        type="medication",
        schedule={"freq": "daily", "time": "09:00"},
        created_at="2026-07-25T10:00:00+08:00",
    )
    assert rd.routine_id == "rtn_001"
    assert rd.active is True

    ro = RoutineOccurrence(
        routine_id="rtn_001",
        title="吃血壓藥",
        type="medication",
        scheduled_at="2026-07-25T09:00:00+08:00",
        status="done",
        completed_at="2026-07-25T09:05:00+08:00",
    )
    assert ro.status == "done"
    # 未完成的 occurrence 不帶完成欄位
    assert "completed_by" not in ro.model_dump(exclude_none=True)


def test_routine_schedule_validation():
    """測試 schedule 依 freq 的欄位規則與時間格式。"""
    weekly = RoutineSchedule(freq="weekly", weekday=3, time="19:00")
    assert weekly.weekday == 3

    with pytest.raises(ValidationError):
        RoutineSchedule(freq="weekly", time="19:00")  # 缺 weekday
    with pytest.raises(ValidationError):
        RoutineSchedule(freq="daily", weekday=3, time="19:00")  # daily 不該帶 weekday
    with pytest.raises(ValidationError):
        RoutineSchedule(freq="once", time="19:00")  # 缺 date
    with pytest.raises(ValidationError):
        RoutineSchedule(freq="daily", time="9:00")  # 時間格式錯


def test_routine_request_models_reject_unknown_fields():
    """server-owned 或未知欄位一律拒絕（docs/api.md）。"""
    with pytest.raises(ValidationError):
        RoutineCreate(
            client_request_id="uuid_123",
            elder_id="eld_001",
            title="吃血壓藥",
            schedule=RoutineSchedule(freq="daily", time="09:00"),
            routine_id="rtn_偽造",
        )
    with pytest.raises(ValidationError):
        RoutineUpdate(client_request_id="uuid_124", created_at="2026-07-25T10:00:00+08:00")
    with pytest.raises(ValidationError):
        RoutineComplete(date="2026/07/25")

    assert RoutineComplete().date is None


def test_daily_summary_models():
    """測試 DailySummaryCreate (DB 寫入模型) 與 DailySummaryResponse (API 回應模型) 驗證與序列化。"""
    sections = {key: None for key in SUMMARY_SECTION_KEYS}
    sections["diet"] = "三餐正常"

    dsc = DailySummaryCreate(
        elder_id="eld_001",
        date="2026-07-31",
        overview="今日狀態良好",
        sections=sections,
        routines={"completed": 1, "missed": 0, "items": []},
        alerts=[],
        interaction_count=3,
        data_status="complete",
        pending_session_count=0,
        input_through_at="2026-07-31T23:50:00+08:00",
        generated_at="2026-07-31T23:50:05+08:00",
    )
    assert dsc.elder_id == "eld_001"
    assert dsc.data_status == "complete"
    assert dsc.sections["diet"] == "三餐正常"

    dsr = DailySummaryResponse(
        elder_id="eld_001",
        date="2026-07-31",
        overview="今日狀態良好",
        sections=sections,
        routines={"completed": 1, "missed": 0, "items": []},
        alerts=[],
        interaction_count=3,
        data_status="complete",
        pending_session_count=0,
        generated_at="2026-07-31T23:50:05+08:00",
    )
    dumped = dsr.model_dump()
    assert dumped["elder_id"] == "eld_001"
    assert "input_through_at" not in dumped
    assert "completeness_rank" not in dumped
    assert dumped["sections"]["diet"] == "三餐正常"

