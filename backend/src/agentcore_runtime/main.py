"""部署包根目錄的進入點。

AgentCore 的 `code_configuration.entry_point` 以 zip 根目錄為基準，因此打包時會把這支檔案
另外複製一份到根目錄（見 terraform/agentcore.tf 的 agent_runtime_source_path）。內容只是
一行 import，實作在 runtime.py。
"""

from src.agentcore_runtime.runtime import app  # noqa: F401


if __name__ == "__main__":
    app.run()
