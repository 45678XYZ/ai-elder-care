"""AWS Bedrock Agent Action Group — Tools Lambda Handler。

規格與定義出處：
- 規格書：docs/llm_tools.md
- Terraform 定義：terraform/bedrock_agent.tf

原理說明：
當 Bedrock Agent (Claude 5 Sonnet) 在對話中判定需要執行特定任務（如查詢行程、標記吃藥完成）時，
AWS 會包裝一個 JSON payload 傳給本 Lambda。
本 Handler 負責解析 Bedrock 傳入的 function 名稱與 parameters，呼叫 shared/db.py 讀寫 DynamoDB，
並回傳 Bedrock 要求的標準格式 JSON。

緊急通知安全機制（醫療級）：
- category="emergency"         : 初次緊急警報，5 分鐘冷卻，寫入 type=safety event
- category="critical_escalation": 狀況急遽惡化，強制繞過冷卻，立即發信，收斂同一 event
- category="mitigation"        : 長者自述緩解，狀態改為「⚠️ 待家屬確認」，不代表解除
- category="routine"/"summary" : 日常通知，無冷卻限制
"""

import json
import os
import time
import uuid
from typing import Any, Dict, Optional

from src.shared import db, sessions, routines
from src.extraction.canonical import safety_alert_key, event_id_for


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
    date_str = params.get("date", time.strftime("%Y-%m-%d"))

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
    date_str = params.get("date", time.strftime("%Y-%m-%d"))
    completed_by = params.get("completed_by", "conversation")

    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    try:
        result = db.complete_routine_with_event(
            elder_id=elder_id,
            routine_id=routine_id,
            routine_date=date_str,
            ts=now_iso,
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
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")

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
# 工具 3.2：停用/刪除例行公事
# -----------------------------------------------------------------------------

def handle_deactivate_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 3.2：停用/刪除長者的例行公事。"""
    elder_id = params.get("elder_id")
    routine_id = params.get("routine_id")
    
    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}
        
    return _apply_routine_update(elder_id, routine_id, {"active": False})


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
            "health_notes": elder_info.get("health_notes", []),
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

        # 處理暱稱更新
        if "nickname" in params and params["nickname"]:
            patch_data["nickname"] = params["nickname"]

        # 處理健康注意事項 (附加至陣列)
        if "health_note_to_add" in params and params["health_note_to_add"]:
            current_health_notes = current_profile.get("health_notes", [])
            new_note = params["health_note_to_add"].strip()
            if new_note and new_note not in current_health_notes:
                current_health_notes.append(new_note)
                patch_data["health_notes"] = current_health_notes

        # 處理生活習慣紀錄 (附加至字串)
        if "habit_note_to_append" in params and params["habit_note_to_append"]:
            current_habit = current_profile.get("habit_note", "").strip()
            new_habit = params["habit_note_to_append"].strip()
            if new_habit:
                if current_habit:
                    patch_data["habit_note"] = f"{current_habit}\n{new_habit}"
                else:
                    patch_data["habit_note"] = new_habit

        if not patch_data:
            return {"status": "error", "message": "沒有提供任何欲更新的檔案欄位"}

        updated_profile = db.update_elder(elder_id, patch_data)
        
        # 準備回傳的簡要格式
        return {
            "status": "success",
            "message": "已成功更新長者個人檔案",
            "updated_fields": list(patch_data.keys()),
            "data": {
                "elder_id": updated_profile.get("elder_id"),
                "nickname": updated_profile.get("nickname"),
                "health_notes": updated_profile.get("health_notes", []),
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
    date_str = params.get("date", time.strftime("%Y-%m-%d"))

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


def _write_safety_event(elder_id: str, alert_id: str, detail: str) -> dict[str, Any]:
    """以 canonical key 寫入 type=safety event，冪等收斂。"""
    canonical_key = safety_alert_key(alert_id)
    event_id = event_id_for(elder_id, canonical_key)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    event, is_new = db.put_event_if_absent({
        "elder_id": elder_id,
        "canonical_event_key": canonical_key,
        "event_id": event_id,
        "ts": now_iso,
        "type": "safety",
        "detail": detail,
        "source": "conversation",
        "extraction_track": "realtime",
    })
    return event


def handle_notify_caregiver(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具七：發送 AWS SNS 即時緊急警報/日常摘要/行程完成通知至照護者。"""
    elder_id = params.get("elder_id")
    category = params.get("category", "emergency")
    message_content = params.get("message", "")
    rag_content = params.get("rag_content", "")
    context_event_id: Optional[str] = params.get("context_event_id")

    if not elder_id or not message_content:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 message"}

    now_ts = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    message_id = None

    try:
        topic_arn = os.environ.get("CAREGIVER_NOTIFY_TOPIC_ARN")

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
            event = _write_safety_event(elder_id, alert_id, event_detail)

            # ③ 更新 In-Memory 狀態鎖（含 alert_id）
            _emergency_state[elder_id] = {
                "alert_id": alert_id,
                "event_id": event["event_id"],
                "notify_ts": now_ts,
                "status": "urgent",
            }

            subject = "🚨【智慧長照緊急警報】長者可能需要即時關懷與協助"
            email_body = _build_emergency_email(elder_id, message_content, rag_content)
            message_id = _publish_sns(topic_arn, subject, email_body)
            print(f"[Emergency] elder={elder_id} alert_id={alert_id} event_id={event['event_id']}")

        elif category == "critical_escalation":
            # ① 解析 alert_id（優先取 Agent 傳入的 context_event_id）
            state = _emergency_state.get(elder_id, {})
            alert_id = _resolve_alert_id(state, context_event_id)

            if alert_id:
                # ② 寫入同一 canonical key，收斂到同一 type=safety event
                escalation_detail = f"🚨🚨【狀況急遽惡化 - 已通報照護者】{message_content}"
                event = _write_safety_event(elder_id, alert_id, escalation_detail)
                active_event_id = event["event_id"]
            else:
                # ③ 若無任何 alert_id，建立新 episode（安全降級：寧可多發一次警報）
                alert_id = f"alert_{uuid.uuid4().hex[:12]}"
                escalation_detail = f"🚨🚨【狀況急遽惡化 - 已通報照護者】{message_content}"
                event = _write_safety_event(elder_id, alert_id, escalation_detail)
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
            message_id = _publish_sns(topic_arn, subject, email_body)
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
            event = _write_safety_event(elder_id, alert_id, mitigation_detail)

            # ③ 更新 In-Memory 狀態為 unverified_mitigation
            _emergency_state[elder_id] = {
                "alert_id": alert_id,
                "event_id": event["event_id"],
                "notify_ts": now_ts,
                "status": "unverified_mitigation",
            }

            subject = "⚠️【智慧長照通知】長者自述緩解 - 請家屬仍需親自確認"
            email_body = _build_mitigation_email(elder_id, message_content)
            message_id = _publish_sns(topic_arn, subject, email_body)
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
            message_id = _publish_sns(topic_arn, subject, email_body)
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


def _publish_sns(topic_arn: Optional[str], subject: str, message: str) -> str:
    """發送 SNS 通知；若 topic_arn 未設定則使用 Mock 模式（開發環境）。"""
    if topic_arn:
        import boto3
        sns_client = boto3.client("sns")
        resp = sns_client.publish(
            TopicArn=topic_arn,
            Subject=subject,
            Message=message
        )
        return resp.get("MessageId", "")
    else:
        mock_id = f"mock-msg-{int(time.time())}"
        print(f"[Mock SNS] Subject: {subject}\n{message[:200]}...")
        return mock_id


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


# 工具分流映射字典 (Function Name -> Handler Function)
# -----------------------------------------------------------------------------

TOOL_HANDLERS = {
    "get_today_routines": handle_get_today_routines,
    "complete_routine": handle_complete_routine,
    "create_routine": handle_create_routine,
    "update_routine": handle_update_routine,
    "deactivate_routine": handle_deactivate_routine,
    "get_recent_events": handle_get_recent_events,
    "get_elder_profile": handle_get_elder_profile,
    "update_elder_profile": handle_update_elder_profile,
    "remind_pending_routines": handle_remind_pending_routines,
    "notify_caregiver": handle_notify_caregiver,
    "get_daily_summaries": handle_get_daily_summaries,
    "get_recent_conversations": handle_get_recent_conversations,
}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Bedrock Action Group 觸發的 Lambda 主進入點。

    Bedrock 傳入格式範例：
    {
      "messageVersion": "1.0",
      "actionGroup": "ElderCareRoutinesTools",
      "function": "complete_routine",
      "parameters": [
        {"name": "elder_id", "type": "string", "value": "eld_001"},
        {"name": "routine_id", "type": "string", "value": "rtn_001"}
      ]
    }
    """
    print(f"[Tools Lambda] Received event: {json.dumps(event, ensure_ascii=False)}")

    action_group = event.get("actionGroup", "")
    function_name = event.get("function", "")
    parameters_list = event.get("parameters", [])

    # 將 Bedrock 傳入的 parameters 陣列轉換為 Python 字典
    param_dict: Dict[str, Any] = {}
    for p in parameters_list:
        p_name = p.get("name")
        p_val = p.get("value")
        if p_name:
            param_dict[p_name] = p_val

    # 若 Payload 中有 sessionId，補充為 elder_id 的預設值
    if "sessionId" in event and "elder_id" not in param_dict:
        param_dict["elder_id"] = event["sessionId"]

    # 尋找對應的工具處理函數
    handler_func = TOOL_HANDLERS.get(function_name)
    if not handler_func:
        result_payload = {
            "status": "error",
            "message": f"未知的工具功能: '{function_name}'"
        }
    else:
        result_payload = handler_func(param_dict)

    # 轉為 JSON 字串以符合 Bedrock Response 格式
    response_body_text = json.dumps(result_payload, ensure_ascii=False)

    # 組裝 AWS Bedrock Agent 指定的標準傳回格式
    bedrock_response = {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "function": function_name,
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": response_body_text
                    }
                }
            }
        }
    }

    print(f"[Tools Lambda] Responding: {json.dumps(bedrock_response, ensure_ascii=False)}")
    return bedrock_response