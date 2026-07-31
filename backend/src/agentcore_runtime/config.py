"""AgentCore Runtime 的環境變數；實際值由 terraform/agentcore.tf 注入。

集中在這裡是為了讓測試能以 monkeypatch 覆寫單一模組，不必逐個 patch os.environ。
"""

import os


AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

# 工具執行仍留在 tools Lambda：notify_caregiver 的緊急冷卻用的是 process 內的
# in-memory 狀態，搬進常駐的 Runtime 會變成跨長者共用，語意與現行行為不同
TOOLS_FUNCTION_NAME = os.environ.get("TOOLS_FUNCTION_NAME", "")

# 託管長期記憶；空字串時 graph 退回不帶 checkpointer 的單輪模式（供本機開發）
AGENT_MEMORY_ID = os.environ.get("AGENT_MEMORY_ID", "")

# 衛教知識庫；空字串時 search_health_knowledge 直接回報未配置，不讓模型誤以為查無資料
KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
KB_RETRIEVE_TOP_K = int(os.environ.get("KB_RETRIEVE_TOP_K", "4"))

AGENT_MODEL_ID = os.environ.get("AGENT_MODEL_ID", "")

# 單輪對話的工具呼叫上限；防模型在 tools ↔ agent 之間無限繞圈把 chat Lambda 的 28 秒耗盡
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "6"))
