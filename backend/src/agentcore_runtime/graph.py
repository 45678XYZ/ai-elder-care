"""對話大腦的狀態機。

兩個節點：`agent` 負責思考與決定要不要呼叫工具，`tools` 負責執行。條件邊在兩者之間循環，
直到模型不再要求工具為止。

`tools_called` 會累積本輪用過的工具名稱，最後由 runtime 換算成 `routines_updated` 與
`safety_alert_triggered` 回給 chat Lambda。舊版是掃 Bedrock trace 的字串來猜，模型改個
措辭就會失準；由圖自己記錄才是可靠的來源。

長期記憶走 AgentCore Memory：`AgentCoreMemorySaver` 是 LangGraph 的 checkpointer，因此
docs/framework.md 的「長期記憶由 AWS AgentCore 服務管理，不自建 DynamoDB memories 表」
仍然成立，同時 LangGraph 的 interrupt／resume 能力也保留著。
"""

import logging
from typing import Annotated, Any, Dict, List, Sequence, TypedDict

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.agentcore_runtime import config
from src.agentcore_runtime.prompts import SYSTEM_PROMPT
from src.agentcore_runtime.tools import build_tools


logger = logging.getLogger(__name__)


class CompanionState(TypedDict):
    """對話狀態。

    `messages` 由 add_messages 累加，因此託管記憶回放時能接續既有對話。
    """

    messages: Annotated[Sequence[AnyMessage], add_messages]
    tools_called: List[str]


def _build_model():
    """建立對話模型。

    ChatBedrockConverse 走 Converse API，與 extraction pipeline 用的是同一套模型識別碼慣例
    （見 terraform/variables.tf 的 bedrock_model_id）。
    """
    return ChatBedrockConverse(
        model=config.AGENT_MODEL_ID,
        region_name=config.AWS_REGION,
    )


def _checkpointer():
    """託管長期記憶的 checkpointer；未配置 memory 時回 None（本機開發走單輪模式）。"""
    if not config.AGENT_MEMORY_ID:
        logger.warning("未配置 AGENT_MEMORY_ID，本輪不接長期記憶")
        return None

    from langgraph_checkpoint_aws import AgentCoreMemorySaver

    return AgentCoreMemorySaver(config.AGENT_MEMORY_ID, region_name=config.AWS_REGION)


def build_graph(elder_id: str):
    """組出綁定該長者的對話圖。

    每輪重建而非全域快取：工具把 elder_id 閉包在內部（見 tools.py），跨長者共用同一份圖
    會把工具呼叫寫到別人的紀錄上。
    """
    tools = build_tools(elder_id)
    model = _build_model().bind_tools(tools)

    def agent_node(state: CompanionState) -> Dict[str, Any]:
        """思考節點：把系統提示擺在最前，讓託管記憶回放的歷史訊息接在後面。"""
        response = model.invoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
        called = [call["name"] for call in getattr(response, "tool_calls", []) or []]
        return {"messages": [response], "tools_called": called}

    def should_continue(state: CompanionState) -> str:
        """還要不要跑工具。

        除了看模型有沒有要求工具，也擋工具呼叫的總次數：模型偶爾會在 agent ↔ tools 之間
        繞圈，而 chat Lambda 只有 28 秒可用，繞不完就整輪失敗、長者得重講一次。
        """
        if len(state["tools_called"]) >= config.MAX_TOOL_ITERATIONS:
            logger.warning("本輪工具呼叫已達上限，直接收斂回覆")
            return END

        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    builder = StateGraph(CompanionState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=_checkpointer())


def final_text(messages: Sequence[AnyMessage]) -> str:
    """取出最後一則助手回覆的純文字。

    Converse API 的 content 可能是字串或 content block 陣列，兩種都要能取出文字；
    工具訊息不算回覆內容。
    """
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type", "text") == "text"
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
    return ""
