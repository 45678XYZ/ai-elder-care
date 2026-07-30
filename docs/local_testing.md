# 智慧長照陪伴系統 — 本地開發與測試沙盒指南 (Local Sandbox Guide)

本指南說明如何在**不依賴 AWS 雲端環境、不消耗 AWS 額度**的情況下，在您的個人電腦本機（本地）搭建完整的 Agent 測試沙盒，並可接入目前最強的模型（如 Anthropic Claude 3.5 Sonnet）或 100% 免費的本地開源模型（如 Qwen 2.5 / Llama 3.1）。

---

## 一、 本地沙盒架構設計

為了在本機模擬 AWS Bedrock AgentCore 的 Memory（記憶）與 Tool Calling（工具箱），我們使用以下本地替代方案：

*   **大腦 (LLM)**：
    *   **最強模型方案**：直接在本地 Python 呼叫 **Anthropic API** (使用 Claude 3.5 Sonnet)。這只需支付極微量的 API Token 費用，完全繞過 AWS 雲端基礎設施。
    *   **100% 免費方案**：使用 **Ollama** 在本機運行開源模型（如 `qwen2.5:7b` 或 `llama3.1:8b`），兩者均內建強大的 Tool Calling 與中文支援。
*   **記憶系統 (Memory)**：在本地 Python 程式碼中，使用一個簡單的 `dict` 或本地 `SQLite` 資料庫，以 `session_id` (長者 ID) 作為 Key 來儲存對話歷史。
*   **工具資料庫 (Database)**：使用本地的 `SQLite` 或 `JSON 檔案` 來模擬 DynamoDB 的六張表（讀寫 routines 與 events）。

---

## 二、 方案 A：100% 免費且本機運行 (使用 Ollama + Qwen 2.5)

### 步驟 1：安裝 Ollama
1. 前往 [Ollama 官網](https://ollama.com) 下載並安裝適用於您作業系統的安裝包。
2. 開啟 Terminal，下載並運行對中文支援極佳、且具備 Tool Calling 能力的模型：
   ```bash
   ollama run qwen2.5:7b
   ```

### 步驟 2：使用 Python 本地 Agent 程式碼
在您的暫存區中，我們已為您寫好了一個本地測試腳本。它能模擬大腦呼叫本地工具與 Session 記憶。

---

## 三、 方案 B：呼叫最強模型 (使用 Anthropic API 金鑰)

如果您有 Anthropic 官方的 API 金鑰（`API Key`），您可以繞過 AWS Bedrock，直接在本地 Python 用官方 SDK 呼叫 **Claude 3.5 Sonnet**：

### 步驟 1：安裝套件
```bash
pip install anthropic
```

### 步驟 2：設定環境變數並執行
```powershell
$env:ANTHROPIC_API_KEY="你的Claude金鑰"
```

---

## 四、 本地 Mock 工具箱與記憶模擬代碼

我們在本地可以使用以下 Python 程式碼架構（模擬 AgentCore 的運作），您可以將其建立在本地的測試檔案中：

```python
import json
from typing import List, Dict, Any

# ==========================================
# 1. 本地 Mock 資料庫 (替代 DynamoDB)
# ==========================================
LOCAL_DB = {
    "routines": [
        {
            "routine_id": "rtn_001",
            "elder_id": "eld_001",
            "title": "吃血壓藥",
            "type": "medication",
            "schedule": {"freq": "daily", "time": "09:00"},
            "status": "pending"
        }
    ],
    "events": []
}

# ==========================================
# 2. 本地實作 LLM 可呼叫的 Tool 函數
# ==========================================
def get_today_routines(elder_id: str) -> str:
    """查詢長者今日行程"""
    items = [r for r in LOCAL_DB["routines"] if r["elder_id"] == elder_id]
    return json.dumps({"date": "2026-07-22", "items": items}, ensure_ascii=False)

def complete_routine(elder_id: str, routine_id: str) -> str:
    """完成特定行程"""
    for r in LOCAL_DB["routines"]:
        if r["routine_id"] == routine_id and r["elder_id"] == elder_id:
            r["status"] = "done"
            # 模擬寫入事件
            LOCAL_DB["events"].append({
                "elder_id": elder_id,
                "type": r["type"],
                "detail": f"已完成: {r['title']}",
                "source": "conversation"
            })
            return json.dumps({"status": "success", "message": f"行程 {routine_id} 已標記完成"}, ensure_ascii=False)
    return json.dumps({"status": "failed", "message": "找不到該行程"}, ensure_ascii=False)

# ==========================================
# 3. 本地模擬 AgentCore Memory 系統
# ==========================================
class LocalSessionMemory:
    """本地 Session 記憶管理器"""
    def __init__(self):
        self.sessions: Dict[str, List[Dict[str, str]]] = {}

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        return self.sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str):
        history = self.get_history(session_id)
        history.append({"role": role, "content": content})
        # 限制記憶長度，只保留最近 10 輪對話
        if len(history) > 20:
            self.sessions[session_id] = history[-20:]
```

---

## 五、 本地語音合成 (TTS) 與 語音辨識 (ASR) 的免付費串接

為了完全脫離雲端進行本地完整測試：

1.  **免費 ASR (語音轉文字)**：
    使用開源的 **Whisper** 在本地進行辨識。您可以安裝 `openai-whisper` 套件：
    ```bash
    pip install openai-whisper
    ```
    在本地 Python 中只需幾行代碼即可把 `.wav` 或 `.m4a` 轉成文字：
    ```python
    import whisper
    model = whisper.load_model("base") # 下載最小的模型
    result = model.transcribe("test.wav")
    print(result["text"])
    ```
2.  **免費 TTS (文字轉語音)**：
    客語可以使用 **OmniVoice API** 的測試額度，而中文如果想在本地免費測試，可以使用微軟 Edge 的免付費 TTS 引擎（**`edge-tts`**，發音極度自然且完全免費）：
    ```bash
    pip install edge-tts
    ```
    執行指令或程式碼即可將文字轉為本地 mp3 檔案：
    ```bash
    edge-tts --text "阿蘭嬤，吃藥時間到囉！" --write-media hello.mp3 --voice zh-TW-HsiaoChenNeural
    ```
