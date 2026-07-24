"""DynamoDB 6 張表之 Pydantic 資料模型定義。

包含 elders, conversations, events, daily_summaries, memories, routines。
此模組為全系統資料 Schema 之單一真理來源 (Single Source of Truth)，
供 API Handlers (Request/Response DTO Validation) 與 shared/db.py 共同引用。
"""

from typing import Literal
from pydantic import BaseModel, Field


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
    ai_prompt_audio_url: str | None = Field(default=None, description="系統發起提醒之 Polly 語音檔 URL (AI 1)")
    elder_audio_s3_key: str | None = Field(default=None, description="長者原始錄音檔上傳至 S3 之檔案路徑 (Elder)")
    ai_respond_audio_url: str | None = Field(default=None, description="AI 最終回應 Polly 語音檔 URL (AI 2)")
    prompt_sent_at: str | None = Field(default=None, description="系統送出提醒發問之時間戳記")
    elder_received_at: str | None = Field(default=None, description="接收到長者輸入之時間戳記 (反應時間分析)")
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
    ai_prompt_audio_url: str | None = Field(default=None, description="系統提醒語音 URL (AI 1)")
    elder_audio_s3_key: str | None = Field(default=None, description="長者錄音 S3 路徑 (Elder)")
    ai_respond_audio_url: str | None = Field(default=None, description="AI 回應語音 URL (AI 2)")
    prompt_sent_at: str | None = Field(default=None, description="系統送出提醒時間")
    elder_received_at: str | None = Field(default=None, description="接收長者輸入時間")
    ai_responded_at: str | None = Field(default=None, description="AI 完成回應時間")
    routines_updated: bool = Field(default=False, description="是否觸發行程更新")
