"""DynamoDB 5 張表之 Pydantic 資料模型定義。

包含 elders, conversations, events, daily_summaries, routines。
此模組為全系統資料 Schema 之單一真理來源 (Single Source of Truth)，
供 API Handlers (Request/Response DTO Validation) 與 shared/db.py 共同引用。

模型分類標示：
- 【API Request】：對外接收的請求體驗證模型，用於 handler 入口校驗
- 【API Response】：對外回應的投影模型，用於 handler 出口洗滌（隱藏內部欄位）
- 【DB Schema】：資料層寫入驗證模型，用於 shared/db.py 寫入前校驗與預設值補充
- 【子模型】：被上述模型引用的共用結構，不單獨對外
"""

import re
from typing import Any, Literal, get_args
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# -----------------------------------------------------------------------------
# 共用 enum 型別
# -----------------------------------------------------------------------------

# 對外的高階事件類別，契約見 docs/api.md 的 EventType。
# 細分類節點（concept_id）由 extraction 的可抽換分類體系資產決定，不在此列舉。
EventType = Literal["diet", "activity", "sleep", "medication", "wellbeing", "safety", "other"]

# daily_summaries.sections 與 EventType 一一對應，順序即為呈現順序。
SUMMARY_SECTION_KEYS: tuple[str, ...] = get_args(EventType)


# =============================================================================
# API Request Models — 對外請求體驗證
# =============================================================================

# -----------------------------------------------------------------------------
# Elders — POST /elders、PATCH /elders/{elder_id}
# -----------------------------------------------------------------------------

class FamilyMember(BaseModel):
    """【子模型】親友背景與備註；被 ElderCreate / ElderUpdate / ElderResponse 引用。"""
    relation: str = Field(..., description="親友稱謂與關係，例如：兒子、孫子")
    name: str = Field(..., description="親友姓名或暱稱，例如：陳志明、小明")
    note: str | None = Field(default=None, description="動態背景備註，例如：在台北工作，每週三來訪")


class ElderCreate(BaseModel):
    """【API Request】POST /elders Request Body。"""
    name: str = Field(..., description="長者真實姓名（必填）")
    nickname: str | None = Field(default=None, description="長者暱稱或慣稱，例如：阿蘭嬤")
    birth_year: int | None = Field(default=None, description="出生年份，例如：1948")
    gender: Literal["male", "female", "other"] | None = Field(default=None, description="性別")
    lang_preference: Literal["zh-TW", "hak"] = Field(default="zh-TW", description="語言偏好（預設 zh-TW）")
    address_region: str | None = Field(default=None, description="居住區域，例如：台北市大安區")
    health_notes: list[str] = Field(default_factory=list, description="健康狀況/病史備註標籤")
    family: list[FamilyMember] = Field(default_factory=list, description="親友背景資訊")
    habit_note: str | None = Field(default=None, description="生活習慣與喜好備註")
    caregiver_ids: list[str] = Field(default_factory=list, description="綁定之照護者 Cognito User ID 列表")


class ElderUpdate(BaseModel):
    """【API Request】PATCH /elders/{elder_id} Request Body（所有欄位皆可選）。"""
    name: str | None = Field(default=None, description="長者真實姓名")
    nickname: str | None = Field(default=None, description="長者暱稱")
    birth_year: int | None = Field(default=None, description="出生年份")
    gender: Literal["male", "female", "other"] | None = Field(default=None, description="性別")
    lang_preference: Literal["zh-TW", "hak"] | None = Field(default=None, description="語言偏好")
    address_region: str | None = Field(default=None, description="居住區域")
    health_notes: list[str] | None = Field(default=None, description="健康狀況/病史備註標籤")
    family: list[FamilyMember] | None = Field(default=None, description="親友背景資訊")
    habit_note: str | None = Field(default=None, description="生活習慣與喜好備註")
    caregiver_ids: list[str] | None = Field(default=None, description="綁定之照護者 Cognito User ID 列表")


class ElderResponse(BaseModel):
    """【API Response】GET /elders、GET /elders/{elder_id}、POST /elders、PATCH /elders/{elder_id} 回應物件。"""
    elder_id: str = Field(..., description="長者唯一識別碼（前綴 eld_）")
    name: str = Field(..., description="長者姓名")
    nickname: str | None = Field(default=None, description="長者暱稱")
    birth_year: int | None = Field(default=None, description="出生年份")
    gender: str | None = Field(default=None, description="性別")
    lang_preference: str = Field(default="zh-TW", description="語言偏好")
    address_region: str | None = Field(default=None, description="居住區域")
    health_notes: list[str] = Field(default_factory=list, description="健康狀況備註")
    family: list[FamilyMember] = Field(default_factory=list, description="親友背景資訊")
    habit_note: str | None = Field(default=None, description="生活習慣與喜好")
    caregiver_ids: list[str] = Field(default_factory=list, description="綁定之照護者 ID 列表")
    created_at: str = Field(..., description="建立時間 (ISO 8601, 台灣時間 +08:00)")
    updated_at: str | None = Field(default=None, description="最後更新時間 (ISO 8601, 台灣時間 +08:00)")


# -----------------------------------------------------------------------------
# Conversations 表與 Chat API 模型
# -----------------------------------------------------------------------------

class ChatAudio(BaseModel):
    """【子模型】POST /chat 音訊輸入；被 ChatRequest 引用。"""
    data: str = Field(..., description="base64 音訊資料")
    format: Literal["m4a", "wav"] = Field(default="m4a", description="音訊格式")


class ChatRequest(BaseModel):
    """【API Request】POST /chat Request Body 模型。"""
    # 必填：turn 的身分由 elder_id + client_request_id 穩定產生，沒有它就沒有冪等性，
    # 斷線重送會把同一句話寫成兩輪對話、做兩次 routine 副作用。
    client_request_id: str = Field(
        ..., min_length=1, description="冪等 UUID；同一次輸入重送沿用同值"
    )
    session_id: str | None = Field(default=None, description="Session ID")
    elder_id: str = Field(..., description="長者 ID")
    lang: Literal["zh-TW", "hak"] = Field(..., description="對話語言 (zh-TW | hak)")
    text: str | None = Field(default=None, description="文字輸入")
    audio: ChatAudio | None = Field(default=None, description="語音輸入")

    @model_validator(mode="after")
    def _validate_input_choice(self) -> "ChatRequest":
        if not self.text and not self.audio:
            raise ValueError("text 與 audio 必須擇一填寫")
        if self.text and self.audio:
            raise ValueError("text 與 audio 不能同時提供")
        return self
















class ConversationItem(BaseModel):
    """【DB Schema】conversations 表 Turn Item 完整資料庫 Schema 定義。"""
    conversation_id: str | None = Field(default=None, description="對話 ID (前綴 cnv_)")
    session_id: str | None = Field(default=None, description="關聯 Session ID (前綴 ses_)")
    elder_id: str = Field(..., description="對話歸屬之長者 ID（必填）")
    record_id: str | None = Field(default=None, description="DynamoDB Sort Key (TURN#cnv_...)")
    conversation_time_key: str | None = Field(default=None, description="DynamoDB GSI Sort Key (<created_at>#<conversation_id>)")
    item_type: str = Field(default="conversation", description="DynamoDB 項目類型 (conversation)")
    created_at: str | None = Field(default=None, description="建立時間 (ISO 8601)")
    client_request_id: str | None = Field(default=None, description="冪等 UUID")

    request_hash: str | None = Field(default=None, description="請求內容 hash")
    request_status: Literal["processing", "completed", "failed"] = Field(
        default="completed", description="turn 處理狀態；統計與摘要只計 completed"
    )
    request_lease_owner: str | None = Field(default=None, description="租約擁有者")
    request_lease_until: str | None = Field(default=None, description="租約到期時間")
    error_http_status: int | None = Field(default=None, description="HTTP 狀態碼")
    error_code: str | None = Field(default=None, description="錯誤代碼")
    error_message: str | None = Field(default=None, description="錯誤訊息")

    source: Literal["elder_initiated", "system_routine_inquiry"] = Field(
        default="elder_initiated", description="對話發起來源（長者主動 / 系統例行公事詢問）"
    )
    user_status: Literal["replied", "no_response"] = Field(
        default="replied", description="長者行為狀態（已回覆 / 逾時無回應）"
    )
    system_status: Literal["success", "failed"] = Field(
        default="success", description="系統技術處理狀態（成功 / 處理失敗）"
    )
    routine_id: str | None = Field(default=None, description="關聯例行公事 ID")

    lang: Literal["zh-TW", "hak"] = Field(default="zh-TW", description="對話語言")
    input_type: Literal["text", "audio"] = Field(default="text", description="輸入類型 (文字 / 語音)")

    elder_transcript: str | None = Field(default=None, description="長者說的話 / 語音轉寫文字 (Elder)")
    ai_respond_text: str | None = Field(default=None, description="AI 最終回應內文 (AI)")
    ai_respond_audio_s3_key: str | None = Field(default=None, description="AI 回應語音 S3 物件路徑 (不存 URL)")

    elder_received_at: str | None = Field(default=None, description="接收到長者發話之時間戳記")
    ai_responded_at: str | None = Field(default=None, description="AI 推理完成送出回應之時間戳記")
    routines_updated: bool = Field(default=False, description="本輪對話是否觸發例行公事狀態更新")

# -----------------------------------------------------------------------------
# Events 表模型
# -----------------------------------------------------------------------------


class EventCreate(BaseModel):
    """【DB Schema】events 表寫入驗證；由 shared/db.py 引用，非對外 API 請求體。"""
    elder_id: str = Field(..., description="長者 ID")
    ts: str = Field(..., description="事件發生的 ISO 8601 時間戳記")
    type: EventType = Field(..., description="高階事件分類")
    concept_id: str | None = Field(default=None, description="分類體系的細分類節點；自動萃取事件必填，API 不暴露")
    taxonomy_version: str | None = Field(default=None, description="寫入當時的分類體系版本；自動萃取事件必填")
    detail: str = Field(..., description="事件自然語言描述")
    structured_detail: dict[str, Any] | None = Field(default=None, description="JSON 結構化細節資訊")
    source: Literal["conversation", "manual"] = Field(default="conversation", description="資料來源")
    canonical_event_key: str | None = Field(default=None, description="Date+Slot+Subject+Predicate 標準化鍵")
    extraction_track: Literal["realtime", "batch", "manual"] = Field(default="batch", description="萃取軌道")
    conversation_id: str | None = Field(default=None, description="關聯之 Turn ID")
    evidence_conversation_ids: list[str] = Field(default_factory=list, description="支持此事件之 Turn IDs")
    session_id: str | None = Field(default=None, description="關聯 Session ID")
    source_chunk_id: str | None = Field(default=None, description="初建 Chunk ID")
    routine_id: str | None = Field(default=None, description="對應例行公事 ID")
    routine_version: int | None = Field(default=None, description="完成時的 Routine 版本號")
    routine_date: str | None = Field(default=None, description="完成的 Routine 日期 YYYY-MM-DD")
    completed_by: Literal["conversation", "elder", "caregiver"] | None = Field(default=None, description="完成角色")
    confidence: float | None = Field(default=None, description="AI 萃取信心度 (0~1)")
    revision: int = Field(default=1, description="版本號，預設 1")


class EventResponse(BaseModel):
    """【API Response】GET /events 回應物件；隱藏萃取內部欄位（concept_id、taxonomy_version 等）。"""
    event_id: str = Field(..., description="事件唯一識別碼 (前綴 evt_)")
    elder_id: str = Field(..., description="長者 ID")
    ts: str = Field(..., description="事件發生時間 (ISO 8601)")
    type: str = Field(..., description="事件分類")
    detail: str = Field(..., description="事件描述")
    source: str = Field(default="conversation", description="事件來源")
    conversation_id: str | None = Field(default=None, description="對話 turn ID")
    routine_id: str | None = Field(default=None, description="例行公事 ID")


# -----------------------------------------------------------------------------
# Daily Summaries 表模型
# -----------------------------------------------------------------------------

class DailySummaryCreate(BaseModel):
    """【DB Schema】daily_summaries 表寫入驗證；由 shared/db.py 寫入前校驗。

    input_through_at 為覆寫優先序依據（docs/api.md），必填；completeness_rank 由 db.py 依
    data_status 自動推導（complete=1、partial=0），不在此暴露。
    """
    elder_id: str = Field(..., description="長者 ID")
    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    overview: str = Field(default="", description="當日總覽")
    sections: dict[str, str | None] = Field(default_factory=dict, description="固定分類區塊，key 與 EventType 對應")
    routines: dict[str, Any] = Field(default_factory=dict, description="例行公事統計 (completed, missed, items)")
    alerts: list[str] = Field(default_factory=list, description="警訊清單")
    interaction_count: int = Field(default=0, description="當日對話輪數")
    data_status: Literal["complete", "partial"] = Field(..., description="資料完整度；覆寫優先序依據，必填")
    pending_session_count: int = Field(default=0, description="待處理 Session 數")
    input_through_at: str = Field(..., description="本摘要承諾納入的資料時間上限 (ISO 8601)；覆寫優先序依據")
    generated_at: str = Field(..., description="生成時間 (ISO 8601)")


class DailySummaryResponse(BaseModel):
    """【API Response】GET /summaries、POST /summaries/generate 回應物件；隱藏 input_through_at 等內部欄位。"""
    elder_id: str = Field(..., description="長者 ID")
    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    overview: str = Field(..., description="當日總覽")
    sections: dict[str, str | None] = Field(..., description="固定分類區塊，key 與 EventType 一一對應，見 SUMMARY_SECTION_KEYS")
    routines: dict[str, Any] = Field(..., description="例行公事完成統計與清單 (completed, missed, items)")
    alerts: list[str] = Field(default_factory=list, description="警訊清單")
    interaction_count: int = Field(default=0, description="當日對話輪數")
    data_status: Literal["complete", "partial"] = Field(default="complete", description="資料完整度")
    pending_session_count: int = Field(default=0, description="待處理 Session 數")
    generated_at: str = Field(..., description="生成時間")



# -----------------------------------------------------------------------------
# Routines 表模型
# -----------------------------------------------------------------------------

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class RoutineSchedule(BaseModel):
    """【子模型】例行公事排程設定；freq 為 discriminator，只接受該頻率適用的欄位。被 RoutineCreate / RoutineUpdate 引用。"""
    model_config = ConfigDict(extra="forbid")

    freq: Literal["daily", "weekly", "once"] = Field(..., description="頻率 (每日/每週/單次)")
    time: str = Field(..., description="時間 (HH:MM，如 09:00)")
    weekday: int | None = Field(default=None, ge=1, le=7, description="星期幾 (1-7，週一為 1，僅 weekly 使用)")
    date: str | None = Field(default=None, description="特定日期 (YYYY-MM-DD，僅 once 使用)")

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not TIME_PATTERN.match(value):
            raise ValueError("time 必須為 HH:MM")
        return value

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str | None) -> str | None:
        if value is not None and not DATE_PATTERN.match(value):
            raise ValueError("date 必須為 YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def _validate_freq_fields(self) -> "RoutineSchedule":
        # 每週多天必須拆成多筆 weekly routine，因此 weekday 只收單一值（見 docs/api.md）
        if self.freq == "weekly" and self.weekday is None:
            raise ValueError("weekly 排程必須指定 weekday")
        if self.freq != "weekly" and self.weekday is not None:
            raise ValueError("只有 weekly 排程可指定 weekday")
        if self.freq == "once" and self.date is None:
            raise ValueError("once 排程必須指定 date")
        if self.freq != "once" and self.date is not None:
            raise ValueError("只有 once 排程可指定 date")
        return self


class RoutineCreate(BaseModel):
    """【API Request】POST /routines Request Body。server-owned 或未知欄位一律拒絕。"""
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(..., description="冪等識別 UUID")
    elder_id: str = Field(..., description="長者 ID")
    title: str = Field(..., min_length=1, description="行程標題 (如：吃血壓藥)")
    type: EventType = Field(default="other", description="分類")
    schedule: RoutineSchedule = Field(..., description="排程設定")
    remind: bool = Field(default=True, description="是否發送提醒通知")


class RoutineUpdate(BaseModel):
    """【API Request】PATCH /routines/{routine_id} Request Body。只開放白名單欄位。"""
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(..., description="冪等識別 UUID")
    title: str | None = Field(default=None, min_length=1, description="行程標題")
    type: EventType | None = Field(default=None, description="分類")
    schedule: RoutineSchedule | None = Field(default=None, description="排程設定")
    remind: bool | None = Field(default=None, description="是否發送提醒")
    active: bool | None = Field(default=None, description="是否啟用")


class RoutineComplete(BaseModel):
    """【API Request】POST /routines/{routine_id}/complete Request Body。"""
    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, description="完成的日期 (YYYY-MM-DD)，預設今天")

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str | None) -> str | None:
        if value is not None and not DATE_PATTERN.match(value):
            raise ValueError("date 必須為 YYYY-MM-DD")
        return value


class RoutineDefinition(BaseModel):
    """【API Response】GET /routines 定義列表回應物件；版本、冪等鍵等內部欄位不外露。"""
    routine_id: str = Field(..., description="例行公事 ID (前綴 rtn_)")
    elder_id: str = Field(..., description="長者 ID")
    title: str = Field(..., description="行程標題")
    type: str = Field(default="other", description="分類")
    schedule: dict[str, Any] = Field(..., description="排程設定")
    remind: bool = Field(default=True, description="是否提醒")
    active: bool = Field(default=True, description="是否啟用")
    created_by: str = Field(default="caregiver", description="建立者角色 (caregiver/conversation)")
    created_at: str = Field(..., description="建立時間 (ISO 8601)")


class RoutineOccurrence(BaseModel):
    """【API Response】GET /routines?date= 當日行程與 POST /routines/{id}/complete 回應物件；狀態於查詢當下推導，不落地保存。"""
    routine_id: str = Field(..., description="例行公事 ID")
    title: str = Field(..., description="行程標題")
    type: str = Field(default="other", description="分類")
    scheduled_at: str = Field(..., description="預定時間 (ISO 8601)")
    status: Literal["pending", "done", "missed"] = Field(..., description="完成狀態")
    completed_at: str | None = Field(default=None, description="完成時間 (ISO 8601)")
    completed_by: str | None = Field(default=None, description="完成角色 (conversation/elder/caregiver)")


# -----------------------------------------------------------------------------
# 統計 Response 模型（GET /stats；由 conversations、routines 與 events 即時彙總）
# -----------------------------------------------------------------------------

class StatsToday(BaseModel):
    """當日互動統計。"""
    interaction_count: int = Field(default=0, description="當日 /chat 對話輪數")
    last_interaction_at: str | None = Field(default=None, description="當日最後一次互動時間 (ISO 8601)；無互動時省略")


class StatsPeriod(BaseModel):
    """區間互動統計。"""
    days: int = Field(..., description="統計天數（含今天）")
    interaction_count: int = Field(default=0, description="區間內 /chat 對話輪數")
    active_days: int = Field(default=0, description="區間內有互動的天數")


class StatsRoutineItem(BaseModel):
    """單一例行公事在區間內的完成度。"""
    routine_id: str = Field(..., description="例行公事 ID")
    title: str = Field(..., description="行程標題")
    completed: int = Field(default=0, description="已完成的 occurrence 數（依 canonical completion event 計數）")
    total: int = Field(default=0, description="區間內排程的 occurrence 數")


class StatsRoutines(BaseModel):
    """例行公事完成度統計。"""
    by_routine: list[StatsRoutineItem] = Field(default_factory=list, description="只列區間內至少排程一次的 routine")


class StatsDailyPoint(BaseModel):
    """逐日趨勢的單日資料點。"""
    date: str = Field(..., description="日期 (YYYY-MM-DD，台灣日界)")
    interaction_count: int = Field(default=0, description="當日 /chat 對話輪數")
    routines_completed: int = Field(default=0, description="當日已完成的 occurrence 數")
    routines_total: int = Field(default=0, description="當日排程的 occurrence 數")


class StatsResponse(BaseModel):
    """統計 Response 物件 (GET /stats)。"""
    elder_id: str = Field(..., description="長者 ID")
    today: StatsToday = Field(..., description="當日互動統計")
    period: StatsPeriod = Field(..., description="區間互動統計")
    routines: StatsRoutines = Field(..., description="例行公事完成度統計")
    daily: list[StatsDailyPoint] = Field(default_factory=list, description="近 N 日趨勢，依日期遞增，區間內每天一筆")
