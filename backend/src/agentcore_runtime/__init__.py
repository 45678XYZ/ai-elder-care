"""AgentCore Runtime：對話大腦的 LangGraph 實作。

取代原本的 Bedrock Agents Classic 託管 agent。部署與 IAM 見 terraform/agentcore.tf，
呼叫方是 `POST /chat` 的 chat Lambda（backend/src/handlers/chat.py）。

模組分工：
- `config`：環境變數集中處，程式不寫死資源識別碼
- `prompts`：人設與語系規則（原本寫在 Terraform 的 agent instruction）
- `tools`：12 個工具的 LangChain 包裝 + 衛教知識庫檢索工具
- `graph`：agent／tools 兩節點的狀態機與託管記憶 checkpointer
- `runtime`：AgentCore 的 `/invocations` 進入點
- `main`：部署包根目錄的進入點薄殼
"""
