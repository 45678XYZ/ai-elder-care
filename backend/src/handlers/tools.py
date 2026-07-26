"""AWS Bedrock Agent Action Group — Tools Lambda Handler。

規格與定義出處：
- 規格書：docs/llm_tools.md
- Terraform 定義：terraform/bedrock_agent.tf

原理說明：
當 Bedrock Agent (Claude 5 Sonnet) 在對話中判定需要執行特定任務（如查詢行程、標記吃藥完成）時，
AWS 會包裝一個 JSON payload 傳給本 Lambda。
本 Handler 負責解析 Bedrock 傳入的 function 名稱與 parameters，呼叫 shared/db.py 讀寫 DynamoDB，
並回傳 Bedrock 要求的標準格式 JSON。
"""

import json
import time
import uuid
from typing import Any, Dict

from src.shared import db


def handle_get_today_routines(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具一：查詢長者指定日期的例行行程清單與動態完成狀態。"""
    elder_id = params.get("elder_id")
    date_str = params.get("date", time.strftime("%Y-%m-%d"))

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        # 呼叫 db.py 的動態行程計算函數
        result = db.get_daily_routines(elder_id, date_str)
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"[Error] handle_get_today_routines 失敗: {e}")
        return {"status": "error", "message": f"查詢失敗: {str(e)}"}


def handle_complete_routine(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具二：標記行程完成，並同步連動寫入 events 表。"""
    elder_id = params.get("elder_id")
    routine_id = params.get("routine_id")
    date_str = params.get("date", time.strftime("%Y-%m-%d"))
    completed_by = params.get("completed_by", "conversation")

    if not elder_id or not routine_id:
        return {"status": "error", "message": "缺少必要參數 elder_id 或 routine_id"}

    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    try:
        # 呼叫 db.py 的原子交易函數，同時更新 routines 狀態並建立 event 記錄
        result = db.complete_routine_with_event(
            elder_id=elder_id,
            routine_id=routine_id,
            date_str=date_str,
            event_id=event_id,
            ts=now_iso,
            source=completed_by,
            detail=f"對話中確認完成行程 (ID: {routine_id})",
            event_type="routine_completion"
        )
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"[Error] handle_complete_routine 失敗: {e}")
        return {"status": "error", "message": f"完成行程操作失敗: {str(e)}"}


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
        "created_at": now_iso
    }

    try:
        # 呼叫 db.py 新增例行公事
        created_item = db.create_routine(routine_item)
        return {"status": "success", "data": created_item}
    except Exception as e:
        print(f"[Error] handle_create_routine 失敗: {e}")
        return {"status": "error", "message": f"建立新行程失敗: {str(e)}"}


def handle_get_recent_events(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具四：查詢長者近期的生活事件與健康記錄歷史。"""
    elder_id = params.get("elder_id")
    event_type = params.get("event_type")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        # 查詢近期的事件歷史 (最多 20 筆)
        items, _ = db.list_events(
            elder_id=elder_id,
            event_type=event_type,
            limit=20
        )
        return {"status": "success", "count": len(items), "data": items}
    except Exception as e:
        print(f"[Error] handle_get_recent_events 失敗: {e}")
        return {"status": "error", "message": f"查詢生活事件失敗: {str(e)}"}


def handle_get_elder_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """工具五：查詢長者的個人檔案、喜好偏好、健康注意事項與家屬成員。"""
    elder_id = params.get("elder_id")

    if not elder_id:
        return {"status": "error", "message": "缺少必要參數 elder_id"}

    try:
        elder_info = db.get_elder(elder_id)
        if not elder_info:
            return {"status": "error", "message": f"找不到長者 (ID: {elder_id}) 的個人檔案"}

        # 整理對 LLM 溫暖對話有幫助的資料
        profile = {
            "elder_id": elder_info.get("elder_id"),
            "name": elder_info.get("name"),
            "nickname": elder_info.get("nickname"),
            "lang_preference": elder_info.get("lang_preference"),
            "health_notes": elder_info.get("health_notes", []),
            "family": elder_info.get("family", []),
            "preferences": elder_info.get("preferences", {})
        }
        return {"status": "success", "data": profile}
    except Exception as e:
        print(f"[Error] handle_get_elder_profile 失敗: {e}")
        return {"status": "error", "message": f"查詢長者檔案失敗: {str(e)}"}


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


# 工具分流映射字典 (Function Name -> Handler Function)
TOOL_HANDLERS = {
    "get_today_routines": handle_get_today_routines,
    "complete_routine": handle_complete_routine,
    "create_routine": handle_create_routine,
    "get_recent_events": handle_get_recent_events,
    "get_elder_profile": handle_get_elder_profile,
    "remind_pending_routines": handle_remind_pending_routines,
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
    param_dict = {}
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
        # 執行具體工具邏輯
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
