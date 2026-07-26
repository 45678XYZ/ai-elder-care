"""DynamoDB 5 張表之 Pydantic 資料模型定義。

包含 elders, conversations, events, daily_summaries, routines。
此模組為全系統資料 Schema 之單一真理來源 (Single Source of Truth)，
供 API Handlers (Request/Response DTO Validation) 與 shared/db.py 共同引用。
"""

from typing import Any, Literal, get_args
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# 共用 enum 型別
# -----------------------------------------------------------------------------

# 對外的高階事件類別，契約見 docs/api.md 的 EventType。
# 細分類節點（concept_id）由 extraction 的可抽換分類體系資產決定，不在此列舉。
EventType = Literal["diet", "activity", "sleep", "medication", "wellbeing", "safety", "other"]

# daily_summaries.sections 與 EventType 一一對應，順序即為呈現順序。
SUMMARY_SECTION_KEYS: tuple[str, ...] = get_args(EventType)


# -----------------------------------------------------------------------------
# Elders 表模型
# -----------------------------------------------------------------------------

class FamilyMember(BaseModel):
    """親友背景與備註。"""
    relation: str = Field(..., description="親友稱謂與關係，例如：兒子、孫子")
    name: str = Field(..., description="親友姓名或暱稱，例如：陳志明、小明")
    note: str | None = Field(default=None, description="動態背景備註，例如：在台北工作，每週三來訪")


class ElderCreate(BaseModel):
    """建立長者 Request Body。"""
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
    """更新長者 (PATCH) Request Body（所有欄位皆可選）。"""
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
    """長者資料完整 Response 物件。"""
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
# Conversations 表模型
# -----------------------------------------------------------------------------

class ConversationCreate(BaseModel):
    """新增/紀錄對話。"""
    elder_id: str = Field(..., description="對話歸屬之長者 ID（必填）")
    source: Literal["elder_initiated", "system_routine_inquiry"] = Field(
        default="elder_initiated", description="對話發起來源（長者主動 / 系統例行公事詢問）"
    )
    user_status: Literal["replied", "no_response"] = Field(
        default="replied", description="長者行為狀態（已回覆 / 逾時無回應）"
    )
    system_status: Literal["success", "failed"] = Field(
        default="success", description="系統技術處理狀態（成功 / 處理失敗）"
    )
    error_message: str | None = Field(default=None, description="系統失敗時之錯誤訊息說明")
    routine_id: str | None = Field(default=None, description="若為系統 Routine 詢問，關聯之例行公事 ID")
    lang: Literal["zh-TW", "hak"] = Field(default="zh-TW", description="對話語言")
    input_type: Literal["text", "audio"] = Field(default="text", description="輸入類型 (文字 / 語音)")
    ai_prompt_text: str | None = Field(default=None, description="系統發起提醒之提示內文 (AI 1)；長者主動發話時為 None")
    elder_transcript: str | None = Field(default=None, description="長者說的話 / 語音轉寫文字 (Elder)；逾時無回應時為 None")
    ai_respond_text: str | None = Field(default=None, description="AI 最終回應/確認內文 (AI 2)")
    ai_prompt_audio_s3_key: str | None = Field(default=None, description="系統提醒語音 S3 物件路徑 (AI 1)")
    elder_audio_s3_key: str | None = Field(default=None, description="長者原始錄音 S3 物件路徑 (Elder)")
    ai_respond_audio_s3_key: str | None = Field(default=None, description="AI 最終回應語音 S3 物件路徑 (AI 2)")
    ai_prompt_audio_url: str | None = Field(default=None, description="系統提醒語音 URL (AI 1)")
    elder_audio_url: str | None = Field(default=None, description="長者原始錄音 URL (Elder)")
    ai_respond_audio_url: str | None = Field(default=None, description="AI 最終回應語音 URL (AI 2)")
    prompt_sent_at: str | None = Field(default=None, description="系統送出提醒發問之時間戳記")
    elder_received_at: str | None = Field(default=None, description="接收到長者輸入之時間戳記 (長者反應時間分析)")
    ai_responded_at: str | None = Field(default=None, description="AI 推理完成送出回應之時間戳記 (後端 Latency 分析)")
    routines_updated: bool = Field(default=False, description="本輪對話是否觸發例行公事狀態更新")


class ConversationResponse(BaseModel):
    """對話紀錄完整 Response 物件。"""
    conversation_id: str = Field(..., description="對話唯一識別碼 (前綴 cnv_)")
    elder_id: str = Field(..., description="長者唯一識別碼 (PK)")
    created_at: str = Field(..., description="建立時間 (SK, ISO 8601, 台灣時間 +08:00)")
    source: str = Field(default="elder_initiated", description="對話發起來源")
    user_status: str = Field(default="replied", description="長者行為狀態")
    system_status: str = Field(default="success", description="系統處理狀態")
    error_message: str | None = Field(default=None, description="系統失敗訊息")
    routine_id: str | None = Field(default=None, description="關聯例行公事 ID")
    lang: str = Field(default="zh-TW", description="對話語言")
    input_type: str = Field(default="text", description="輸入類型")
    ai_prompt_text: str | None = Field(default=None, description="系統發起提醒之提示內文 (AI 1)")
    elder_transcript: str | None = Field(default=None, description="長者說的話 / 語音轉寫文字 (Elder)")
    ai_respond_text: str | None = Field(default=None, description="AI 最終回應/確認內文 (AI 2)")
    ai_prompt_audio_s3_key: str | None = Field(default=None, description="系統提醒語音 S3 路徑 (AI 1)")
    elder_audio_s3_key: str | None = Field(default=None, description="長者錄音 S3 路徑 (Elder)")
    ai_respond_audio_s3_key: str | None = Field(default=None, description="AI 回應語音 S3 路徑 (AI 2)")
    ai_prompt_audio_url: str | None = Field(default=None, description="動態簽發 15 分鐘有效 S3 Presigned URL (AI 1)")
    elder_audio_url: str | None = Field(default=None, description="動態簽發 15 分鐘有效 S3 Presigned URL (Elder)")
    ai_respond_audio_url: str | None = Field(default=None, description="動態簽發 15 分鐘有效 S3 Presigned URL (AI 2)")
    prompt_sent_at: str | None = Field(default=None, description="系統送出提醒時間")
    elder_received_at: str | None = Field(default=None, description="接收長者輸入時間")
    ai_responded_at: str | None = Field(default=None, description="AI 完成回應時間")
    routines_updated: bool = Field(default=False, description="是否觸發行程更新")


# -----------------------------------------------------------------------------
# Events 表模型
# -----------------------------------------------------------------------------

class EventCreate(BaseModel):
    """建立生活事件 Request Body。"""
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
    """生活事件 Response 物件 (GET /events)。"""
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

class DailySummaryResponse(BaseModel):
    """每日摘要 Response 物件 (GET /summaries)。"""
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

class RoutineSchedule(BaseModel):
    """例行公事排程設定。"""
    freq: Literal["daily", "weekly", "once"] = Field(..., description="頻率 (每日/每週/單次)")
    time: str | None = Field(default=None, description="時間 (HH:MM，如 09:00)")
    weekday: int | None = Field(default=None, description="星期幾 (1-7，週一為 1，僅 weekly 使用)")
    date: str | None = Field(default=None, description="特定日期 (YYYY-MM-DD，僅 once 使用)")


class RoutineCreate(BaseModel):
    """建立例行公事 Request Body (POST /routines)。"""
    client_request_id: str = Field(..., description="冪等識別 UUID")
    elder_id: str = Field(..., description="長者 ID")
    title: str = Field(..., description="行程標題 (如：吃血壓藥)")
    type: EventType = Field(default="other", description="分類")
    schedule: RoutineSchedule = Field(..., description="排程設定")
    remind: bool = Field(default=True, description="是否發送提醒通知")


class RoutineUpdate(BaseModel):
    """更新/停用例行公事 Request Body (PATCH /routines/{id})。"""
    client_request_id: str = Field(..., description="冪等識別 UUID")
    title: str | None = Field(default=None, description="行程標題")
    type: EventType | None = Field(default=None, description="分類")
    schedule: RoutineSchedule | None = Field(default=None, description="排程設定")
    remind: bool | None = Field(default=None, description="是否發送提醒")
    active: bool | None = Field(default=None, description="是否啟用")


class RoutineResponse(BaseModel):
    """例行公事定義與當日動態行程 Response 物件。"""
    routine_id: str = Field(..., description="例行公事 ID (前綴 rtn_)")
    elder_id: str = Field(..., description="長者 ID")
    title: str = Field(..., description="行程標題")
    type: str = Field(default="other", description="分類")
    schedule: RoutineSchedule | dict[str, Any] | None = Field(default=None, description="排程設定")
    remind: bool = Field(default=True, description="是否提醒")
    active: bool = Field(default=True, description="是否啟用")
    created_by: str = Field(default="caregiver", description="建立者角色")
    created_at: str | None = Field(default=None, description="建立時間")
    scheduled_at: str | None = Field(default=None, description="預定時間 (ISO 8601)")
    status: Literal["pending", "done", "missed"] | None = Field(default=None, description="當日完成狀態")
    completed_at: str | None = Field(default=None, description="完成時間 (ISO 8601)")
    completed_by: str | None = Field(default=None, description="完成角色 (conversation/elder/caregiver)")
