"""AgentCore Runtime 的 `/invocations` 進入點。

請求與回應的契約（呼叫端是 backend/src/handlers/chat.py 的 invoke_agent_brain）：

    request  {"elder_id", "text", "lang", "local_time", "session_key"}
    response {"reply_text", "routines_updated"}

回一般 JSON 不做 SSE：chat Lambda 會等整段回覆收完才進 TTS，中間沒有逐字的消費者，
串流只是徒增兩邊的解析複雜度。

`/ping` 健康檢查由 BedrockAgentCoreApp 代管，不需自行實作。
"""

import logging
from typing import Any, Dict

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_core.messages import HumanMessage

from src.agentcore_runtime import config
from src.agentcore_runtime.graph import build_graph, final_text
from src.agentcore_runtime.prompts import build_turn_prefix
from src.agentcore_runtime.tools import ROUTINE_MUTATING_TOOLS


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# 模型沒能產出文字時的保底回覆；與 chat.py 舊行為一致，長者至少聽得到一句話
FALLBACK_REPLY = "抱歉，我剛才沒有聽清，您可以再說一次嗎？"


@app.entrypoint
def invoke(payload: Dict[str, Any]) -> Dict[str, Any]:
    """跑一輪對話並回傳結果。"""
    elder_id = (payload or {}).get("elder_id") or ""
    text = (payload or {}).get("text") or ""

    if not elder_id or not text:
        # 缺欄位是呼叫端的問題，回明確錯誤而不是讓模型對著空字串瞎猜
        return {
            "reply_text": FALLBACK_REPLY,
            "routines_updated": False,
            "error": "MISSING_INPUT",
        }

    turn_prefix = build_turn_prefix(payload.get("local_time"), payload.get("lang"))
    inputs: Dict[str, Any] = {"tools_called": []}
    messages = []
    if turn_prefix:
        # 情境前綴與長者原話分開：時間戳每輪不同，混進同一則訊息會一起寫進長期記憶
        messages.append(HumanMessage(content=turn_prefix))
    messages.append(HumanMessage(content=text))
    inputs["messages"] = messages

    # thread_id 決定託管記憶的對話串；actor_id 決定記憶的歸屬對象
    session_key = payload.get("session_key") or elder_id
    run_config = {"configurable": {"thread_id": session_key, "actor_id": elder_id}}

    graph = build_graph(
        elder_id,
        session_id=payload.get("session_id"),
        conversation_id=payload.get("conversation_id"),
    )
    result = graph.invoke(inputs, config=run_config)

    tools_called = set(result.get("tools_called") or [])
    reply_text = final_text(result.get("messages") or []) or FALLBACK_REPLY

    return {
        "reply_text": reply_text,
        "routines_updated": bool(tools_called & ROUTINE_MUTATING_TOOLS),
    }


if __name__ == "__main__":
    app.run()
