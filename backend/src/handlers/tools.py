"""對話大腦的工具箱 — Tools Lambda Handler。

規格與定義出處：
- 規格書：docs/llm_tools.md
- Terraform 定義：terraform/lambda.tf（Lambda 本體）、terraform/agentcore.tf（呼叫端權限）

原理說明：
當對話大腦在推理中判定需要執行特定任務（如查詢行程、標記吃藥完成）時，AgentCore Runtime
（backend/src/agentcore_runtime/tools.py）以 lambda:InvokeFunction 傳一個 JSON payload 給本
Lambda。本 Handler 負責分派到對應的 handle_* 函式，呼叫 shared/db.py 讀寫 DynamoDB，並把結果
以 JSON 回傳。

工具邏輯留在 Lambda 而非搬進常駐的 Runtime，是為了保住下方 `_emergency_state` 的語意——
它依賴 Lambda 的短生命週期，搬進常駐容器會變成跨長者、跨 session 共用同一份記憶體。

緊急通知安全機制（醫療級）：
- category="emergency"         : 初次緊急警報，5 分鐘冷卻，寫入 type=safety event
- category="critical_escalation": 狀況急遽惡化，強制繞過冷卻，立即發信，收斂同一 event
- category="mitigation"        : 長者自述緩解，狀態改為「⚠️ 待家屬確認」，不代表解除
- category="routine"/"summary" : 日常通知，無冷卻限制
"""

import json
import os
import ssl
import time
import urllib.request
import uuid
from typing import Any, Dict, Optional

from src.shared import db, sessions, routines
from src.shared.models import health_note_texts
from src.extraction.canonical import safety_alert_key, event_id_for

import boto3

_USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
_cognito_client = None


def _get_cognito_client():
    global _cognito_client
    if _cognito_client is None:
        _cognito_client = boto3.client("cognito-idp")
    return _cognito_client


def _get_caregiver_emails(elder_id: str) -> list[str]:
    """從 elder 的 caregiver_ids 查 Cognito 取得每位照護者的 email。"""
    elder = db.get_elder(elder_id)
    if not elder:
        return []
    caregiver_ids = elder.get("caregiver_ids", [])
    if not caregiver_ids or not _USER_POOL_ID:
        return []

    emails = []
    client = _get_cognito_client()
    for sub in caregiver_ids:
        try:
            resp = client.admin_get_user(
                UserPoolId=_USER_POOL_ID,
                Username=sub,
            )
            for attr in resp.get("UserAttributes", []):
                if attr["Name"] == "email":
                    emails.append(attr["Value"])
                    break
        except Exception as e:
            print(f"[Warn] 查詢照護者 {sub} email 失敗: {e}")
    return emails


# -----------------------------------------------------------------------------
# Lambda Warm Start In-Memory 緊急狀態鎖
# 結構: { elder_id: { "alert_id": str, "event_id": str, "notify_ts": float,
#                     "status": "urgent" | "unverified_mitigation" } }
# 注意：Cold Start 後清空為安全降級（不發誤報優於誤解除警報）
# -----------------------------------------------------------------------------
_emergency_state: Dict[str, Dict[str, Any]] = {}

# 5 分鐘冷卻期（秒）
_EMERGENCY_COOLDOWN_SECS = 300


def _build_emergency_email(elder_id: str, message_content: str, rag_content: str = "") -> str:
    """組裝「🚨 緊急警報」Email 內文（版面分級：人事時地在上，RAG 折疊至附錄）。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    rag_section = ""
    if rag_content:
        rag_section = f"""
------------------------------------------------------------
📖【AI 輔助衛教參考資訊（僅供參考）】
{rag_content}

⚠️ 免責聲明：本資訊由 AI 依據衛教資料輔助檢索，僅供參考。
   緊急醫療情況請優先尋求專業醫護人員診治，請勿以本資訊取代醫療判斷。"""

    return f"""============================================================
🚨 智慧長照 AI 關懷系統 - 即時緊急警報通知
============================================================
⚠️  若情況危急，請立即撥打 119，不要猶豫！

【事件資訊】
• 長者編號：{elder_id}
• 通報時間：{now_str} (UTC+8)

------------------------------------------------------------
【緊急事件詳情】
{message_content}

------------------------------------------------------------
【建議立即處置】
1. 請立刻致電長者或聯繫同住者／管理員確認現場狀況。
2. 評估是否需要協助撥打 119 救護車。
3. 請勿依賴長者自述「沒事」—請親自確認後再行判斷。
============================================================{rag_section}"""


def _build_mitigation_email(elder_id: str, message_content: str) -> str:
    """組裝「⚠️ 長者自述緩解（待家屬確認）」Email 內文。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""============================================================
⚠️  智慧長照 AI 關懷系統 - 長者自述緩解通知（待家屬確認）
============================================================
【重要提醒】此通知並非代表警報完全解除。
長者的口頭回報不足以確認安全，請家屬仍需親自聯繫確認。

【長者自述】
{message_content}

• 長者編號：{elder_id}
• 自述時間：{now_str} (UTC+8)

------------------------------------------------------------
請登入 App 確認長者狀況後，點選「確認平安並結案」按鈕。
在家屬確認之前，本次警報狀態維持「⚠️ 待家屬確認」。
============================================================="""


def _build_escalation_email(elder_id: str, message_content: str) -> str:
    """組裝「🚨🚨 狀況急遽惡化警報」Email 內文。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""============================================================
🚨🚨 智慧長照 AI 關懷系統 - 狀況急遽惡化緊急警報
============================================================
⚠️  請立即撥打 119！此為升級警報，情況比初次通報更為嚴重！

【事件升級詳情】
{message_content}

• 長者編號：{elder_id}
• 惡化通報時間：{now_str} (UTC+8)

------------------------------------------------------------
此警報已繞過冷卻期強制發出，代表長者狀況出現新的嚴重症狀。
請立即採取行動！
============================================================="""


# -----------------------------------------------------------------------------
# 工具一：查詢長者指定日期的例行行程清單與動態完成狀態
# -----------------------------------------------------------------------------

def handle_get_today_routines(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具一：查詢長者指定日期的例行行程清單與動態完成狀態。"""
    elder_id = params.get("elder_id")
    date_str = params.get("date", routines.today())

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        result = db.get_daily_routines(elder_id, date_str)
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"[Error] handle_get_today_routines 失敗: {e}")
        return {"status": "error", "message": f"查詢失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具二：標記行程完成，並同步連動寫入 events 表
# -----------------------------------------------------------------------------

def handle_complete_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具二：標記行程完成，並同步連動寫入 events 表。"""
    elder_id = params.get("elder_id")
    routine_id = params.get("routine_id")
    date_str = params.get("date", routines.today())
    completed_by = params.get("completed_by", "conversation")

    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}

    now = routines.now_iso()

    try:
        result = db.complete_routine_with_event(
            elder_id=elder_id,
            routine_id=routine_id,
            routine_date=date_str,
            ts=now,
            completed_by=completed_by,
            detail=f"對話中確認完成行程 (ID: {routine_id})",
            event_type="routine_completion",
            extraction_track="realtime",
        )
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"[Error] handle_complete_routine 失敗: {e}")
        return {"status": "error", "message": f"完成行程操作失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具三：為長者建立新的例行公事
# -----------------------------------------------------------------------------

def handle_create_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具三：為長者建立新的例行公事。"""
    elder_id = params.get("elder_id")
    title = params.get("title")
    routine_type = params.get("type", "other")
    time_str = params.get("time", "09:00")
    freq = params.get("freq", "daily")
    specific_date = params.get("date")

    if not elder_id or not title:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 title"}

    routine_id = f"rtn_{uuid.uuid4().hex[:12]}"
    now_iso = routines.now_iso()

    schedule_data: Dict[str, Any] = {"freq": freq, "time": time_str}
    if freq == "once" and specific_date:
        schedule_data["date"] = specific_date

    routine_item = {
        "routine_id": routine_id,
        "elder_id": elder_id,
        "title": title,
        "type": routine_type,
        "schedule": schedule_data,
        "active": True,
        "remind": True,
        "created_by": "conversation",
        "updated_by": "conversation",
        "canonical_action_key": f"create_{routine_id}",
        "change_request_id": f"chg_{uuid.uuid4().hex[:12]}",
        "request_hash": "ai_tool_creation",
        "created_at": now_iso,
        "updated_at": now_iso,
        "schema_version": 1,
    }

    try:
        created_item = db.create_routine(routine_item)
        return {"status": "success", "data": created_item}
    except Exception as e:
        print(f"[Error] handle_create_routine 失敗: {e}")
        return {"status": "error", "message": f"建立新行程失敗: {str(e)}"}

# -----------------------------------------------------------------------------
# 工具 3.1：更新例行公事
# -----------------------------------------------------------------------------

def _apply_routine_update(elder_id: str, routine_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    try:
        versions = db.list_routine_versions(routine_id)
        if not versions:
            return {"status": "error", "message": "找不到指定的例行公事"}
            
        current = versions[-1]
        if current.get("elder_id") != elder_id:
            return {"status": "error", "message": "資料不符，無法修改此行程"}
            
        now = routines.now_iso()
        next_version = int(current["version"]) + 1
        active = changes.get("active", current.get("active", True))
        
        item = dict(current)
        item.update(changes)
        
        item.update({
            "version": next_version,
            "is_current": True,
            "effective_from": now,
            "version_time_key": routines.version_time_key(now, routine_id, next_version),
            "current_sort_key": routines.current_sort_key(
                active, current.get("created_at", now), routine_id
            ),
            "updated_by": "conversation",
            "canonical_action_key": f"update_{routine_id}",
            "change_request_id": f"chg_{uuid.uuid4().hex[:12]}",
            "request_hash": "ai_tool_update",
            "updated_at": now,
        })
        item.pop("effective_to", None)
        
        updated = db.replace_current_routine_version(current, item)
        return {"status": "success", "data": updated}
    except Exception as e:
        print(f"[Error] _apply_routine_update 失敗: {e}")
        return {"status": "error", "message": f"操作行程失敗: {str(e)}"}


def handle_update_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 3.1：修改長者的例行公事。"""
    elder_id = params.get("elder_id")
    routine_id = params.get("routine_id")
    
    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}
        
    changes: Dict[str, Any] = {}
    if "title" in params: changes["title"] = params["title"]
    if "type" in params: changes["type"] = params["type"]
    if "remind" in params:
        # 處理 Bedrock 有可能傳來 string 型別的 true/false
        remind_val = params["remind"]
        if isinstance(remind_val, str):
            remind_val = remind_val.lower() == "true"
        changes["remind"] = remind_val
    if "active" in params:
        active_val = params["active"]
        if isinstance(active_val, str):
            active_val = active_val.lower() == "true"
        changes["active"] = active_val
        
    freq = params.get("freq")
    time_str = params.get("time")
    specific_date = params.get("date")
    
    if freq and time_str:
        schedule_data: Dict[str, Any] = {"freq": freq, "time": time_str}
        if freq == "once" and specific_date:
            schedule_data["date"] = specific_date
        changes["schedule"] = schedule_data
        
    if not changes:
        return {"status": "error", "message": "沒有提供任何欲修改的欄位"}
        
    return _apply_routine_update(elder_id, routine_id, changes)


# -----------------------------------------------------------------------------
# 工具 3.2：刪除例行公事
# -----------------------------------------------------------------------------

def handle_delete_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 3.2：刪除長者的例行公事（真刪除，若要恢復則重新建立）。"""
    elder_id = params.get("elder_id")
    routine_id = params.get("routine_id")

    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}

    try:
        versions = db.list_routine_versions(routine_id)
        if not versions:
            return {"status": "error", "message": "找不到指定的例行公事"}
        if versions[-1].get("elder_id") != elder_id:
            return {"status": "error", "message": "資料不符，無法刪除此行程"}

        db.delete_routine(routine_id)
        return {"status": "success", "message": f"已刪除例行公事 {routine_id}"}
    except Exception as e:
        print(f"[Error] handle_delete_routine 失敗: {e}")
        return {"status": "error", "message": f"刪除行程失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具四：查詢長者近期的生活事件與健康記錄歷史
# -----------------------------------------------------------------------------

def handle_get_recent_events(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具四：查詢長者近期的生活事件與健康記錄歷史。"""
    elder_id = params.get("elder_id")
    event_type = params.get("event_type")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        items, _ = db.list_events(
            elder_id=elder_id,
            event_type=event_type,
            limit=20
        )
        return {"status": "success", "count": len(items), "data": items}
    except Exception as e:
        print(f"[Error] handle_get_recent_events 失敗: {e}")
        return {"status": "error", "message": f"查詢生活事件失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具五：查詢長者的個人檔案、喜好偏好、健康注意事項與家屬成員
# -----------------------------------------------------------------------------

def handle_get_elder_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具五：查詢長者的個人檔案、喜好偏好、健康注意事項與家屬成員。"""
    elder_id = params.get("elder_id")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        elder_info = db.get_elder(elder_id)
        if not elder_info:
            return {"status": "error", "message": f"找不到長者 (ID: {elder_id}) 的個人檔案"}

        profile = {
            "elder_id": elder_info.get("elder_id"),
            "name": elder_info.get("name"),
            "nickname": elder_info.get("nickname"),
            "gender": elder_info.get("gender"),
            "birth_year": elder_info.get("birth_year"),
            "lang_preference": elder_info.get("lang_preference"),
            "address_region": elder_info.get("address_region"),
            # 攤平成文字：note_id 對模型沒有意義，留著只會被當成內容處理
            "health_notes": health_note_texts(elder_info.get("health_notes")),
            "family": elder_info.get("family", []),
            "habit_note": elder_info.get("habit_note", ""),
        }
        return {"status": "success", "data": profile}

    except Exception as e:
        print(f"[Error] handle_get_elder_profile 失敗: {e}")
        return {"status": "error", "message": f"查詢長者檔案失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具 5.1：更新長者的個人檔案、健康注意事項與生活習慣紀錄
# -----------------------------------------------------------------------------

def handle_update_elder_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 5.1：更新長者的個人檔案（如新增健康注意事項、生活習慣或暱稱）。"""
    elder_id = params.get("elder_id")
    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        current_profile = db.get_elder(elder_id)
        if not current_profile:
            return {"status": "error", "message": f"找不到長者 (ID: {elder_id}) 的個人檔案"}

        patch_data = {}
        updated_fields = []

        # 處理語言偏好
        if "lang_preference" in params and params["lang_preference"] in ("zh-TW", "hak"):
            patch_data["lang_preference"] = params["lang_preference"]

        # 處理客語腔調
        _VALID_DIALECTS = {
            "htia_sixian", "htia_hailu", "htia_dapu",
            "htia_raoping", "htia_zhaoan", "htia_nansixian",
        }
        if "hakka_dialect" in params and params["hakka_dialect"] in _VALID_DIALECTS:
            patch_data["hakka_dialect"] = params["hakka_dialect"]

        # 處理暱稱更新
        if "nickname" in params and params["nickname"]:
            patch_data["nickname"] = params["nickname"]

        # 處理生活習慣紀錄 (附加至字串)
        if "habit_note_to_append" in params and params["habit_note_to_append"]:
            current_habit = (current_profile.get("habit_note") or "").strip()
            new_habit = params["habit_note_to_append"].strip()
            if new_habit:
                if current_habit:
                    patch_data["habit_note"] = f"{current_habit}\n{new_habit}"
                else:
                    patch_data["habit_note"] = new_habit

        # 處理健康注意事項：走原子 append，不做 read-modify-write。
        # 這一項是 AI 依長輩談話補上的，照護者同時可能正在 App 上刪別的項目，
        # 整份覆寫會讓其中一邊的結果無聲消失（見 db.append_health_note）。
        new_note = (params.get("health_note_to_add") or "").strip()
        appended = False
        if new_note:
            # 舊資料是純字串陣列，新資料是物件，去重時兩種都要看得懂
            existing = {
                n.get("text") if isinstance(n, dict) else n
                for n in (current_profile.get("health_notes") or [])
            }
            if new_note not in existing:
                db.append_health_note(elder_id, {"text": new_note, "source": "agent"})
                appended = True
                updated_fields.append("health_notes")

        if not patch_data and not appended:
            return {"status": "error", "message": "沒有提供任何欲更新的檔案欄位"}

        if patch_data:
            updated_profile = db.update_elder(elder_id, patch_data)
            updated_fields.extend(patch_data.keys())
        else:
            # 只 append 健康註記時不必再寫一次，重讀拿最新內容即可
            updated_profile = db.get_elder(elder_id) or {}

        # 準備回傳的簡要格式
        return {
            "status": "success",
            "message": "已成功更新長者個人檔案",
            "updated_fields": updated_fields,
            "data": {
                "elder_id": updated_profile.get("elder_id"),
                "nickname": updated_profile.get("nickname"),
                "lang_preference": updated_profile.get("lang_preference", "zh-TW"),
                "hakka_dialect": updated_profile.get("hakka_dialect", "htia_sixian"),
                "health_notes": health_note_texts(updated_profile.get("health_notes")),
                "habit_note": updated_profile.get("habit_note", "")
            }
        }

    except Exception as e:
        print(f"[Error] handle_update_elder_profile 失敗: {e}")
        return {"status": "error", "message": f"更新長者檔案失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具六：查詢長者今日尚未完成 (pending) 的例行行程並回傳提醒事項
# -----------------------------------------------------------------------------

def handle_remind_pending_routines(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具六：查詢長者今日尚未完成 (pending) 的例行行程並回傳提醒事項。"""
    elder_id = params.get("elder_id")
    date_str = params.get("date", routines.today())

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        daily = db.get_daily_routines(elder_id, date_str)
        items = daily.get("items", [])
        pending_items = [i for i in items if i.get("status") == "pending"]

        return {
            "status": "success",
            "date": date_str,
            "pending_count": len(pending_items),
            "pending_routines": pending_items
        }
    except Exception as e:
        print(f"[Error] handle_remind_pending_routines 失敗: {e}")
        return {"status": "error", "message": f"查詢待提醒行程失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具七：發送 AWS SNS 即時緊急警報/日常摘要/行程完成通知至照護者
# 醫療級安全機制：
#   - "emergency"          : 5 分鐘冷卻；寫入 type=safety event；產生 alert_id 回傳給 Agent
#   - "critical_escalation": 強制繞過冷卻；收斂同一 alert_id 的 event
#   - "mitigation"         : 長者自述緩解；收斂同一 alert_id 的 event；改為 ⚠️ 待家屬確認
#   - "routine"/"summary"  : 日常通知，無冷卻，不寫 DB 安全事件
#
# canonical key 格式：SAFETY#{alert_id}（由 safety_alert_key 產生）
# alert_id 由 emergency 首次產生，回傳給 Agent，後續 escalation/mitigation 帶入同一 alert_id
# 確保同一警報情節的 emergency → escalation → mitigation 收斂到同一筆 event
# -----------------------------------------------------------------------------

def _resolve_alert_id(state: dict, context_event_id: str | None) -> str | None:
    """解析 current alert_id：優先取 Agent 傳入的 context_event_id，否則取 state 的 alert_id。"""
    if context_event_id:
        return context_event_id
    return state.get("alert_id")


def _write_safety_event(
    elder_id: str,
    alert_id: str,
    detail: str,
    *,
    confidence: float = 1.0,
    session_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """以 canonical key 寫入 type=safety event，冪等收斂。"""
    canonical_key = safety_alert_key(alert_id)
    event_id = event_id_for(elder_id, canonical_key)
    now_iso = routines.now_iso()
    item: dict[str, Any] = {
        "elder_id": elder_id,
        "canonical_event_key": canonical_key,
        "event_id": event_id,
        "ts": now_iso,
        "type": "safety",
        "detail": detail,
        "source": "conversation",
        "extraction_track": "realtime",
        "confidence": confidence,
    }
    if session_id:
        item["session_id"] = session_id
    if conversation_id:
        item["conversation_id"] = conversation_id
        item["evidence_conversation_ids"] = [conversation_id]
    event, is_new = db.put_event_if_absent(item)
    return event


def handle_notify_caregiver(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具七：發送 AWS SNS 即時緊急警報/日常摘要/行程完成通知至照護者。"""
    elder_id = params.get("elder_id")
    category = params.get("category", "emergency")
    message_content = params.get("message", "")
    rag_content = params.get("rag_content", "")
    context_event_id: Optional[str] = params.get("context_event_id")
    _session_id: Optional[str] = params.get("_session_id")
    _conversation_id: Optional[str] = params.get("_conversation_id")

    if not elder_id or not message_content:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 message"}

    now_ts = time.time()
    message_id = None

    try:
        # ------------------------------------------------------------------
        # 分流：依 category 執行不同安全邏輯
        # ------------------------------------------------------------------

        if category == "emergency":
            # ① 檢查冷卻期 (5 分鐘)
            state = _emergency_state.get(elder_id, {})
            last_notify_ts = state.get("notify_ts", 0)
            if (now_ts - last_notify_ts) < _EMERGENCY_COOLDOWN_SECS:
                remaining = int(_EMERGENCY_COOLDOWN_SECS - (now_ts - last_notify_ts))
                print(f"[Cooldown] elder={elder_id} 處於冷卻期，剩餘 {remaining} 秒，攔截重複發送")
                return {
                    "status": "throttled",
                    "elder_id": elder_id,
                    "message": f"冷卻期內攔截（剩餘 {remaining} 秒），避免重複通知洗版",
                    "active_event_id": state.get("event_id"),
                    "alert_id": state.get("alert_id"),
                }

            # ② 產生 alert_id 並寫入 type=safety event
            alert_id = f"alert_{uuid.uuid4().hex[:12]}"
            event_detail = f"🚨【緊急警報已通報照護者】{message_content}"
            event = _write_safety_event(elder_id, alert_id, event_detail, session_id=_session_id, conversation_id=_conversation_id)

            # ③ 更新 In-Memory 狀態鎖（含 alert_id）
            _emergency_state[elder_id] = {
                "alert_id": alert_id,
                "event_id": event["event_id"],
                "notify_ts": now_ts,
                "status": "urgent",
            }

            subject = "🚨【智慧長照緊急警報】長者可能需要即時關懷與協助"
            email_body = _build_emergency_email(elder_id, message_content, rag_content)
            message_id = _publish_to_caregivers(elder_id, subject, email_body)
            print(f"[Emergency] elder={elder_id} alert_id={alert_id} event_id={event['event_id']}")

        elif category == "critical_escalation":
            # ① 解析 alert_id（優先取 Agent 傳入的 context_event_id）
            state = _emergency_state.get(elder_id, {})
            alert_id = _resolve_alert_id(state, context_event_id)

            if alert_id:
                # ② 寫入同一 canonical key，收斂到同一 type=safety event
                escalation_detail = f"🚨🚨【狀況急遽惡化 - 已通報照護者】{message_content}"
                event = _write_safety_event(elder_id, alert_id, escalation_detail, session_id=_session_id, conversation_id=_conversation_id)
                active_event_id = event["event_id"]
            else:
                # ③ 若無任何 alert_id，建立新 episode（安全降級：寧可多發一次警報）
                alert_id = f"alert_{uuid.uuid4().hex[:12]}"
                escalation_detail = f"🚨🚨【狀況急遽惡化 - 已通報照護者】{message_content}"
                event = _write_safety_event(elder_id, alert_id, escalation_detail, session_id=_session_id, conversation_id=_conversation_id)
                active_event_id = event["event_id"]

            # ④ 更新狀態鎖
            _emergency_state[elder_id] = {
                "alert_id": alert_id,
                "event_id": active_event_id,
                "notify_ts": now_ts,
                "status": "urgent",
            }

            subject = "🚨🚨【智慧長照緊急警報】長者狀況急遽惡化，請立即處置"
            email_body = _build_escalation_email(elder_id, message_content)
            message_id = _publish_to_caregivers(elder_id, subject, email_body)
            print(f"[Escalation] elder={elder_id} alert_id={alert_id} event_id={active_event_id}")

        elif category == "mitigation":
            # ① Context Matching：確認確實有一個未結案的緊急事件
            state = _emergency_state.get(elder_id, {})
            alert_id = _resolve_alert_id(state, context_event_id)
            active_status = state.get("status")

            if not alert_id or active_status not in ("urgent",):
                print(f"[Mitigation Ignored] elder={elder_id} 無未結案緊急警報，忽略 mitigation 請求")
                return {
                    "status": "ignored",
                    "elder_id": elder_id,
                    "message": "目前無未結案緊急警報，無需發送緩解通知",
                }

            # ② 寫入同一 canonical key，收斂到同一 type=safety event
            mitigation_detail = (f"⚠️【長者自述緩解 - 待家屬確認】{message_content} "
                                 f"(alert_id={alert_id})")
            event = _write_safety_event(elder_id, alert_id, mitigation_detail, session_id=_session_id, conversation_id=_conversation_id)

            # ③ 更新 In-Memory 狀態為 unverified_mitigation
            _emergency_state[elder_id] = {
                "alert_id": alert_id,
                "event_id": event["event_id"],
                "notify_ts": now_ts,
                "status": "unverified_mitigation",
            }

            subject = "⚠️【智慧長照通知】長者自述緩解 - 請家屬仍需親自確認"
            email_body = _build_mitigation_email(elder_id, message_content)
            message_id = _publish_to_caregivers(elder_id, subject, email_body)
            print(f"[Mitigation] elder={elder_id} alert_id={alert_id} event_id={event['event_id']}")

        elif category in ("routine", "summary"):
            # 日常通知，無冷卻限制，不寫 DB 安全事件
            subject_map = {
                "routine": "📋【智慧長照行程通知】長者今日行程完成狀態",
                "summary": "📖【智慧長照每日摘要】長者今日健康與生活紀錄"
            }
            subject = subject_map[category]
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            email_body = (
                f"長者編號: {elder_id}\n通知類別: {category}\n"
                f"時間: {now_str}\n\n{message_content}"
            )
            message_id = _publish_to_caregivers(elder_id, subject, email_body)
            print(f"[{category.capitalize()}] elder={elder_id} 日常通知發送成功")

        else:
            return {"status": "error", "message": f"未知的通知類別: '{category}'"}

        return {
            "status": "success",
            "elder_id": elder_id,
            "category": category,
            "message_id": message_id,
            "detail": f"已成功發送 {category} 通知給照護者",
        }

    except Exception as e:
        print(f"[Error] handle_notify_caregiver 失敗: {e}")
        return {"status": "error", "message": f"發送照護者通知失敗: {str(e)}"}


_SNS_TOPIC_PREFIX = os.environ.get("SNS_TOPIC_PREFIX", "")
_sns_client = None


def _get_sns_client():
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns")
    return _sns_client


def _get_or_create_elder_topic(elder_id: str) -> str | None:
    """取得或建立 per-elder SNS topic，回傳 topic ARN。"""
    if not _SNS_TOPIC_PREFIX:
        return None
    topic_name = f"{_SNS_TOPIC_PREFIX}-{elder_id.replace('_', '-')}"
    sns = _get_sns_client()
    try:
        resp = sns.create_topic(Name=topic_name)
        return resp["TopicArn"]
    except Exception as e:
        print(f"[Error] 建立 elder topic 失敗: {e}")
        return None


def _ensure_caregivers_subscribed(topic_arn: str, elder_id: str) -> None:
    """確保該長者的所有照護者已訂閱其 SNS topic（冪等）。"""
    emails = _get_caregiver_emails(elder_id)
    sns = _get_sns_client()
    for email in emails:
        try:
            sns.subscribe(
                TopicArn=topic_arn,
                Protocol="email",
                Endpoint=email,
                ReturnSubscriptionArn=True,
            )
        except Exception as e:
            print(f"[Warn] SNS subscribe {email} 失敗: {e}")


def _publish_to_caregivers(elder_id: str, subject: str, message: str) -> str:
    """發送通知到 per-elder SNS topic，只有綁定的照護者會收到。

    流程：
    1. 取得或建立該長者專屬的 SNS topic
    2. 確保照護者 email 已訂閱（首次會收確認信，點一次即永久有效）
    3. Publish 到 per-elder topic
    """
    topic_arn = _get_or_create_elder_topic(elder_id)
    if not topic_arn:
        mock_id = f"mock-msg-{int(time.time())}"
        print(f"[Mock SNS] Subject: {subject}\n{message[:200]}...")
        return mock_id

    _ensure_caregivers_subscribed(topic_arn, elder_id)

    sns = _get_sns_client()
    resp = sns.publish(
        TopicArn=topic_arn,
        Subject=subject,
        Message=message,
    )
    return resp.get("MessageId", "")


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 工具八：查詢長者近幾日每日健康摘要（供大腦提供縱向健康趨勢參考）
# -----------------------------------------------------------------------------

def handle_get_daily_summaries(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具八：查詢長者近幾日的每日健康摘要，讓大腦能參考歷史紀錄提供更貼心的對話。

    預設查詢過去 3 天（含今天）的摘要；可透過 days 參數調整，最多 7 天。
    """
    elder_id = params.get("elder_id")
    days = int(params.get("days", 3))

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    # 限制最多查 7 天，避免資料過大影響 LLM Context
    days = max(1, min(days, 7))

    try:
        from datetime import datetime, timedelta, timezone
        TZ_TAIPEI = timezone(timedelta(hours=8))
        today = datetime.now(TZ_TAIPEI)
        to_date = today.strftime("%Y-%m-%d")
        from_date = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        summaries, _ = db.list_daily_summaries(elder_id, from_date, to_date)


        # 整理為大腦易讀的精簡格式
        formatted = []
        for s in summaries:
            formatted.append({
                "date": s.get("date"),
                "overview": s.get("overview", "（無摘要）"),
                "routines": s.get("routines", {}),
                "data_status": s.get("data_status", "unknown"),
                "sections": {
                    k: v for k, v in (s.get("sections") or {}).items() if v
                }
            })

        return {
            "status": "success",
            "elder_id": elder_id,
            "from_date": from_date,
            "to_date": to_date,
            "count": len(formatted),
            "summaries": formatted
        }
    except Exception as e:
        print(f"[Error] handle_get_daily_summaries 失敗: {e}")
        return {"status": "error", "message": f"查詢每日摘要失敗: {str(e)}"}


def handle_get_recent_conversations(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具九：查詢長者最近幾句對話內容，讓 AI 在 Bedrock session 過期後仍能回憶當前對話脈絡。

    只回傳目前 session 內最新 limit 筆已完成的對話（預設 8 句），並僅保留 elder_transcript、
    ai_respond_text 與時間，避免無關 metadata 浪費 LLM Context Window。
    """
    elder_id = params.get("elder_id")
    limit = int(params.get("limit", 8))

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    # 安全上限：最多 15 句，避免 token 爆炸
    limit = max(1, min(limit, 15))

    try:
        conversations = sessions.get_recent_turns(elder_id, limit=limit)

        # 整理為對話格式，只保留對 AI 有用的欄位（已是時間正序：舊→新）
        turns = []
        for c in conversations:
            turn = {"time": c.get("created_at", "")[:16]}  # 只取 YYYY-MM-DDTHH:MM
            if c.get("elder_transcript"):
                turn["elder"] = c["elder_transcript"]
            if c.get("ai_respond_text"):
                turn["ai"] = c["ai_respond_text"]
            turns.append(turn)

        return {
            "status": "success",
            "elder_id": elder_id,
            "count": len(turns),
            "note": "以下為此次對話最近紀錄（舊→新），供您回顧剛才談話內容",
            "turns": turns,
        }
    except Exception as e:
        print(f"[Error] handle_get_recent_conversations 失敗: {e}")
        return {"status": "error", "message": f"查詢對話紀錄失敗: {str(e)}"}


# -----------------------------------------------------------------------------
# 工具十三：取得天氣預報（中央氣象署 Open Data）
# -----------------------------------------------------------------------------

_CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
_CWA_FORECAST_URL = (
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
)


def _cwa_ssl_context() -> ssl.SSLContext:
    """氣象署專用的 TLS context：解除 Python 3.13 新增的 X509 格式嚴格檢查。

    Python 3.13 起 `ssl.create_default_context()` 預設帶上 VERIFY_X509_STRICT，它依
    RFC 5280 要求鏈上的 CA 憑證必須有 Subject Key Identifier。氣象署的憑證由
    TWCA Secure SSL CA 簽發，該鏈缺這個欄位，於是握手被拒：

        [SSL: CERTIFICATE_VERIFY_FAILED] Missing Subject Key Identifier

    這在 python3.11 的執行環境不會發生（當時沒有這個 flag），是 runtime 升到 3.13
    之後才浮現的——升級的理由見 terraform/lambda.tf（av/numpy 的 arm64 wheel）。

    只關掉這一項：主機名稱比對、憑證鏈信任與效期驗證全部維持，因此不等於停用 TLS 驗證。
    憑證換成有 SKI 的那天，這個函式可以直接刪掉。
    """
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx

_REGION_TO_CWA_LOCATION = {
    "基隆": "基隆市", "台北": "臺北市", "臺北": "臺北市",
    "新北": "新北市", "桃園": "桃園市", "新竹": "新竹縣",
    "苗栗": "苗栗縣", "台中": "臺中市", "臺中": "臺中市",
    "彰化": "彰化縣", "南投": "南投縣", "雲林": "雲林縣",
    "嘉義": "嘉義縣", "台南": "臺南市", "臺南": "臺南市",
    "高雄": "高雄市", "屏東": "屏東縣", "宜蘭": "宜蘭縣",
    "花蓮": "花蓮縣", "台東": "臺東縣", "臺東": "臺東縣",
    "澎湖": "澎湖縣", "金門": "金門縣", "連江": "連江縣",
}


def _resolve_cwa_location(address_region: str | None) -> str:
    """從 elder profile 的 address_region 解析出氣象署使用的地區名稱。"""
    if not address_region:
        return "臺北市"
    for keyword, cwa_name in _REGION_TO_CWA_LOCATION.items():
        if keyword in address_region:
            return cwa_name
    return "臺北市"


def handle_get_weather_forecast(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具十三：取得長者所在地區的天氣預報。"""
    elder_id = params.get("elder_id")
    location = params.get("location")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    if not _CWA_API_KEY:
        return {"status": "error", "message": "天氣服務未配置（缺少 CWA_API_KEY）"}

    if not location:
        try:
            elder = db.get_elder(elder_id)
            location = _resolve_cwa_location(
                elder.get("address_region") if elder else None
            )
        except Exception:
            location = "臺北市"

    url = (
        f"{_CWA_FORECAST_URL}"
        f"?Authorization={_CWA_API_KEY}"
        f"&locationName={urllib.request.quote(location)}"
    )

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5, context=_cwa_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Error] handle_get_weather_forecast 呼叫氣象署失敗: {e}")
        return {"status": "error", "message": "天氣資料暫時無法取得"}

    records = data.get("records", {})
    locations = records.get("location", [])
    if not locations:
        return {"status": "error", "message": f"找不到 {location} 的天氣資料"}

    loc_data = locations[0]
    weather_elements = {
        elem["elementName"]: elem for elem in loc_data.get("weatherElement", [])
    }

    forecast_list = []
    wx = weather_elements.get("Wx", {}).get("time", [])
    min_t = weather_elements.get("MinT", {}).get("time", [])
    max_t = weather_elements.get("MaxT", {}).get("time", [])
    pop = weather_elements.get("PoP", {}).get("time", [])

    for i, period in enumerate(wx):
        entry = {
            "start_time": period.get("startTime", ""),
            "end_time": period.get("endTime", ""),
            "weather": period["parameter"]["parameterName"],
        }
        if i < len(min_t):
            entry["temp_low"] = int(min_t[i]["parameter"]["parameterName"])
        if i < len(max_t):
            entry["temp_high"] = int(max_t[i]["parameter"]["parameterName"])
        if i < len(pop):
            entry["rain_prob"] = int(pop[i]["parameter"]["parameterName"])
        forecast_list.append(entry)

    return {
        "status": "success",
        "location": location,
        "forecast": forecast_list,
    }


# -----------------------------------------------------------------------------
# 工具十四：根據時間範圍查詢生活事件
# -----------------------------------------------------------------------------

def handle_get_events_by_time(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具十四：根據指定時間範圍查詢長者的生活事件。"""
    elder_id = params.get("elder_id")
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    event_type = params.get("event_type")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}
    if not start_date or not end_date:
        return {"status": "error", "message": "缺少必要參數 start_date 或 end_date"}

    try:
        items, _ = db.list_events(
            elder_id=elder_id,
            from_date=start_date,
            to_date=end_date,
            event_type=event_type,
            limit=50,
        )
        return {
            "status": "success",
            "count": len(items),
            "period": {"start": start_date, "end": end_date},
            "data": items,
        }
    except Exception as e:
        print(f"[Error] handle_get_events_by_time 失敗: {e}")
        return {"status": "error", "message": f"查詢事件失敗: {str(e)}"}


# 工具分流映射字典 (Function Name -> Handler Function)
# -----------------------------------------------------------------------------

TOOL_HANDLERS = {
    "get_today_routines": handle_get_today_routines,
    "complete_routine": handle_complete_routine,
    "create_routine": handle_create_routine,
    "update_routine": handle_update_routine,
    "delete_routine": handle_delete_routine,
    "get_recent_events": handle_get_recent_events,
    "get_elder_profile": handle_get_elder_profile,
    "update_elder_profile": handle_update_elder_profile,
    "remind_pending_routines": handle_remind_pending_routines,
    "notify_caregiver": handle_notify_caregiver,
    "get_daily_summaries": handle_get_daily_summaries,
    "get_recent_conversations": handle_get_recent_conversations,
    "get_weather_forecast": handle_get_weather_forecast,
    "get_events_by_time": handle_get_events_by_time,
}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AgentCore Runtime 觸發的 Lambda 主進入點。

    傳入格式（由 backend/src/agentcore_runtime/tools.py 組出）：
    {
      "tool": "complete_routine",
      "params": {"elder_id": "eld_001", "routine_id": "rtn_001", ...}
    }

    `elder_id` 一律由 Runtime 從請求 payload 注入，不是模型填的參數——模型填錯就會寫到
    別位長者的紀錄上。
    """
    print(f"[Tools Lambda] Received event: {json.dumps(event, ensure_ascii=False)}")

    tool_name = event.get("tool", "")
    params: Dict[str, Any] = event.get("params") or {}

    handler_func = TOOL_HANDLERS.get(tool_name)
    if not handler_func:
        result_payload = {
            "status": "error",
            "message": f"未知的工具功能: '{tool_name}'"
        }
    else:
        result_payload = handler_func(params)

    print(f"[Tools Lambda] Responding: {json.dumps(result_payload, ensure_ascii=False)}")
    return result_payload