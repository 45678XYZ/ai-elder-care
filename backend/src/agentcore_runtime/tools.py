"""供對話大腦呼叫的工具。

12 個業務工具的邏輯仍在 tools Lambda（backend/src/handlers/tools.py），這裡只做 LangChain
包裝並以 `lambda:InvokeFunction` 轉呼叫。不把邏輯搬進 Runtime 的原因：`handle_notify_caregiver`
的 5 分鐘緊急冷卻用的是 process 內的 `_emergency_state`，Lambda 的短生命週期正是它現行語意的
前提；搬進常駐容器會變成跨長者、跨 session 共用同一份記憶體。

第 13 個工具 `search_health_knowledge` 直接呼叫 Bedrock Knowledge Base 的 Retrieve，取代原本
Classic agent 的 knowledge base association。

`elder_id` 不是工具參數：它由 Runtime 從請求 payload 注入。原本的 Action Group 把 elder_id
列為模型必填參數，模型填錯就會寫到別位長者的紀錄上；由呼叫端注入才是唯一安全的做法。
工具規格見 docs/llm_tools.md。
"""

import json
import logging
from typing import Any, Dict, List, Tuple

import boto3
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from src.agentcore_runtime import config


logger = logging.getLogger(__name__)

_lambda_client = None
_bedrock_agent_runtime = None


def get_lambda_client():
    """取得 Lambda Client 實例（容器常駐，重用連線）。"""
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda", region_name=config.AWS_REGION)
    return _lambda_client


def get_bedrock_agent_runtime():
    """取得 Bedrock Agent Runtime Client 實例（衛教知識庫檢索用）。"""
    global _bedrock_agent_runtime
    if _bedrock_agent_runtime is None:
        _bedrock_agent_runtime = boto3.client(
            "bedrock-agent-runtime", region_name=config.AWS_REGION
        )
    return _bedrock_agent_runtime


# -----------------------------------------------------------------------------
# 工具規格
#
# 每個參數是 (型別, 是否必填, 給模型看的說明)。說明文字直接沿用原本 Action Group 的內容，
# 那是照著 docs/llm_tools.md 的契約寫的，別在這裡另外改寫。
# -----------------------------------------------------------------------------

ParamSpec = Dict[str, Tuple[type, bool, str]]

TOOL_SPECS: List[Tuple[str, str, ParamSpec]] = [
    (
        "get_today_routines",
        "Retrieve a list of scheduled routines and their completion status for the elder on a given date.",
        {
            "date": (str, True, "查詢的日期，格式為 YYYY-MM-DD，例如 2026-07-20"),
        },
    ),
    (
        "complete_routine",
        "Mark a specific routine as completed and log a life event for the elder.",
        {
            "routine_id": (str, True, "要完成的行程 ID，例如 rtn_001"),
            "date": (str, True, "完成的日期，格式為 YYYY-MM-DD"),
            "completed_by": (str, True, "完成行程的角色，口語回報一律填 conversation"),
        },
    ),
    (
        "create_routine",
        "Create a new scheduled routine (either one-time or recurring) for the elder.",
        {
            "title": (str, True, "行程的標題或內容，例如：吃血壓藥、看心臟科"),
            "type": (str, True, "行程類型分類：medication, diet, activity, wellbeing, other"),
            "time": (str, True, "行程時間，格式為 HH:MM，例如 15:30"),
            "freq": (str, True, "頻率：daily, weekly, once"),
            "date": (str, False, "如果是單次(once)行程，必須提供日期 YYYY-MM-DD；每日或每週則免"),
        },
    ),
    (
        "update_routine",
        "Update an existing scheduled routine (e.g., change time, title, or frequency) for the elder.",
        {
            "routine_id": (str, True, "要修改的行程 ID，例如 rtn_001"),
            "title": (str, False, "行程的標題或內容"),
            "type": (str, False, "行程類型分類：medication, diet, activity, wellbeing, other"),
            "time": (str, False, "行程時間，格式為 HH:MM"),
            "freq": (str, False, "頻率：daily, weekly, once"),
            "date": (str, False, "單次行程的日期 YYYY-MM-DD"),
            "remind": (bool, False, "是否發送提醒"),
            "active": (bool, False, "是否啟用"),
        },
    ),
    (
        "delete_routine",
        "Permanently delete an existing scheduled routine for the elder. If the elder wants it back later, create a new one.",
        {
            "routine_id": (str, True, "要刪除的行程 ID，例如 rtn_001"),
        },
    ),
    (
        "get_recent_events",
        "Retrieve recent life events, activities, and recorded health signals for the elder.",
        {
            "event_type": (
                str,
                False,
                "可選的事件類型過濾：routine_completion, wellbeing, activity, family, diet, other",
            ),
        },
    ),
    (
        "get_elder_profile",
        "Retrieve personal preferences, hobbies, health notes, and family members of the elder.",
        {},
    ),
    (
        "update_elder_profile",
        "Update the elder's profile, including adding new health notes, appending to lifestyle habits, "
        "changing their nickname, or switching language preference based on conversation. "
        "Only set lang_preference/hakka_dialect when the elder EXPLICITLY asks to switch.",
        {
            "health_note_to_add": (
                str,
                False,
                "欲新增的健康注意事項（如：對特定藥物過敏、最近膝蓋痛）。將附加至陣列。",
            ),
            "habit_note_to_append": (
                str,
                False,
                "欲補充的生活習慣與喜好（如：喜歡喝溫開水、不吃牛肉）。將附加至既有字串。",
            ),
            "nickname": (str, False, "長者希望被稱呼的新暱稱。"),
            "lang_preference": (
                str,
                False,
                "語言偏好：zh-TW（華語）或 hak（客語）。僅在長者明確表示想切換語言時才填。",
            ),
            "hakka_dialect": (
                str,
                False,
                "客語腔調：htia_sixian（四縣）、htia_hailu（海陸）、htia_dapu（大埔）、"
                "htia_raoping（饒平）、htia_zhaoan（詔安）、htia_nansixian（南四縣）。"
                "僅在長者明確指定腔調時才填。",
            ),
        },
    ),
    (
        "remind_pending_routines",
        "Check and retrieve pending scheduled routines for the elder to generate warm reminders.",
        {
            "date": (str, False, "可選的查詢日期，格式為 YYYY-MM-DD，預設為今天"),
        },
    ),
    (
        "notify_caregiver",
        "Send SNS notification to caregiver. Use category to control safety behavior:\n"
        "- emergency: First-time urgent alert (fall/chest pain/cannot move). Has 5-min cooldown. Writes DB event.\n"
        "- critical_escalation: Condition worsening (new bleeding/fainting/severe pain). BYPASSES cooldown. "
        "Use when elder reports new severe symptoms after initial emergency.\n"
        "- mitigation: Elder verbally says they feel better. Sets status to WARNING (pending caregiver "
        "confirmation). Does NOT resolve the alert. Requires active emergency to exist.\n"
        "- routine: Scheduled task completion digest.\n"
        "- summary: Daily health summary report.\n"
        "IMPORTANT: Only caregivers (not elders) can fully resolve an alert via the App.",
        {
            "category": (
                str,
                True,
                "通知類別：emergency | critical_escalation | mitigation | routine | summary",
            ),
            "message": (str, True, "要推播給照護者的詳細訊息內容（請包含事件的人事時地）"),
            "context_event_id": (
                str,
                False,
                "選填。用於 mitigation 或 critical_escalation 時，傳入對應的 alert_id"
                "（由系統在 emergency 觸發時回傳），確保收斂到同一筆 type=safety event。"
                "格式為 alert_<hex>，例如 alert_a1b2c3d4e5f6。",
            ),
            "rag_content": (
                str,
                False,
                "選填。來自 search_health_knowledge 的相關急救或照護指南內容。"
                "將折疊附加至 Email 附錄（附免責聲明），不影響信件主要人事時地資訊版面。",
            ),
        },
    ),
    (
        "get_daily_summaries",
        "Retrieve recent daily health summaries for the elder to understand health trends over multiple days. "
        "Use this when the elder or caregiver asks about recent health status, trends, or when you need context "
        "about the elder's health over the past few days.",
        {
            "days": (
                int,
                False,
                "查詢最近幾天的摘要，預設為 3 天（含今天），最多 7 天。",
            ),
        },
    ),
    (
        "get_recent_conversations",
        "Retrieve the most recent conversation turns with the elder. Use this tool when you feel you have lost "
        "context of the current conversation, for example after a session timeout, to recall what was discussed "
        "earlier in this session.",
        {
            "limit": (int, False, "要回顧最近幾句對話，預設為 8，最多 15。建議用預設值即可。"),
        },
    ),
    (
        "get_weather_forecast",
        "Get the current weather forecast for the elder's area. Use when the elder asks about weather, "
        "temperature, rain, or whether to bring an umbrella/wear warm clothes. Also useful for proactive "
        "care reminders related to weather (e.g., cold snap warning, heat stroke prevention).",
        {
            "location": (
                str,
                False,
                "氣象署地區名稱（如：臺北市、高雄市）。不填則自動從長者居住地取得。",
            ),
        },
    ),
    (
        "get_events_by_time",
        "Query the elder's life events within a specific date range. Use when the elder asks about what "
        "happened on particular days (e.g., 'Did I take my medicine last Tuesday?', 'What exercise did I do "
        "this week?'). Unlike get_recent_events which returns the latest 20, this tool filters by exact dates.",
        {
            "start_date": (str, True, "查詢起始日期，格式為 YYYY-MM-DD"),
            "end_date": (str, True, "查詢結束日期，格式為 YYYY-MM-DD"),
            "event_type": (
                str,
                False,
                "可選的事件類型過濾：routine_completion, wellbeing, activity, family, diet, safety, other",
            ),
        },
    ),
]

# 呼叫後代表 routine 定義或當日狀態已變更；chat.py 依此回 routines_updated
ROUTINE_MUTATING_TOOLS = frozenset(
    {"complete_routine", "create_routine", "update_routine", "delete_routine"}
)


def _args_model(tool_name: str, params: ParamSpec):
    """把工具規格轉成 pydantic model，供 LangChain 產生工具 schema。"""
    fields: Dict[str, Any] = {}
    for param_name, (param_type, required, description) in params.items():
        if required:
            fields[param_name] = (param_type, Field(description=description))
        else:
            fields[param_name] = (param_type | None, Field(default=None, description=description))
    return create_model(f"{tool_name}_args", **fields)


def invoke_tools_lambda(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """同步呼叫 tools Lambda 並回傳其 JSON 結果。

    失敗不拋例外而是回一個帶 status=error 的結果：讓模型看得到失敗並改口安撫長者，
    比整輪對話 500 掉、長者只聽到系統錯誤要好。
    """
    if not config.TOOLS_FUNCTION_NAME:
        return {"status": "error", "message": "工具服務未配置"}

    try:
        response = get_lambda_client().invoke(
            FunctionName=config.TOOLS_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps({"tool": tool_name, "params": params}, ensure_ascii=False).encode(
                "utf-8"
            ),
        )
        payload = json.loads(response["Payload"].read().decode("utf-8"))
    except Exception:
        logger.exception("呼叫工具失敗：tool=%s", tool_name)
        return {"status": "error", "message": "工具暫時無法使用"}

    # Lambda 本身拋例外時 invoke 仍回 200，錯誤在 FunctionError 欄位
    if response.get("FunctionError"):
        logger.error("工具執行失敗：tool=%s payload=%s", tool_name, payload)
        return {"status": "error", "message": "工具執行失敗"}

    return payload


def _make_tool(elder_id: str, tool_name: str, description: str, params: ParamSpec) -> StructuredTool:
    """把單一工具規格包成 LangChain 工具；elder_id 由此閉包注入，不交給模型。"""

    def _run(**kwargs: Any) -> str:
        # 未填的選填參數不傳下去：tools Lambda 的 handler 以 key 是否存在判斷要不要更新欄位
        supplied = {k: v for k, v in kwargs.items() if v is not None}
        supplied["elder_id"] = elder_id
        result = invoke_tools_lambda(tool_name, supplied)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        func=_run,
        name=tool_name,
        description=description,
        args_schema=_args_model(tool_name, params),
    )


def _make_knowledge_tool() -> StructuredTool:
    """衛教知識庫檢索工具。

    取代原本掛在 Classic agent 上的 knowledge base association。何時該檢索寫在
    prompts.py 的系統提示裡——那裡沒有原本 association description 的 200 bytes 上限。
    """

    def _run(query: str) -> str:
        if not config.KNOWLEDGE_BASE_ID:
            return json.dumps(
                {"status": "error", "message": "衛教知識庫未配置"}, ensure_ascii=False
            )
        try:
            response = get_bedrock_agent_runtime().retrieve(
                knowledgeBaseId=config.KNOWLEDGE_BASE_ID,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": config.KB_RETRIEVE_TOP_K}
                },
            )
        except Exception:
            logger.exception("衛教知識庫檢索失敗：query=%s", query)
            return json.dumps(
                {"status": "error", "message": "知識庫暫時無法使用"}, ensure_ascii=False
            )

        passages = [
            item.get("content", {}).get("text", "")
            for item in response.get("retrievalResults", [])
        ]
        passages = [p for p in passages if p]
        return json.dumps(
            {"status": "success", "count": len(passages), "passages": passages},
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        func=_run,
        name="search_health_knowledge",
        description=(
            "Search the health education knowledge base for elder care guidance: chronic disease care "
            "(hypertension, diabetes, stroke, asthma, COPD, osteoporosis, metabolic syndrome), dementia, "
            "fall prevention, assistive devices, oral care, nutrition, medication concepts, seasonal health, "
            "and long-term care services in Taiwan (respite care, transportation, home care, subsidy "
            "applications). Use the elder's own wording as the query."
        ),
        args_schema=_args_model(
            "search_health_knowledge",
            {"query": (str, True, "要查詢的衛教或長照問題，用長者的原話即可")},
        ),
    )


def build_tools(elder_id: str) -> List[StructuredTool]:
    """組出本輪對話可用的工具清單；elder_id 綁定在工具內部。"""
    tools: List[StructuredTool] = [
        _make_tool(elder_id, name, description, params)
        for name, description, params in TOOL_SPECS
    ]
    tools.append(_make_knowledge_tool())
    return tools
