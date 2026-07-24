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
