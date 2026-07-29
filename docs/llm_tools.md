# Bedrock Agent 智慧工具箱 (Action Groups / Tools) 規格說明書

本文件定義供 **Amazon Bedrock Agent (Claude 3.5)** 自動調用的後端工具（Action Groups / Tools）。當長者在語音對話中提到與「例行公事（用藥、量血壓等）」或「新建行程（約會、看醫生）」相關的意圖時，Agent 會自動選擇並呼叫對應的工具。

所有工具的執行邏輯均透過一個共用的 **Tools Lambda** (或直接在 `chat` 專案中調用) 進行，並直接讀寫 DynamoDB 的 `routines` 與 `events` 表。

---

## 1. 工具清單與 LLM 調用契機

| 工具名稱 (Tool Name) | 功能描述 (Description for LLM) | 調用契機 (Triggering Intent) |
|---|---|---|
| `get_today_routines` | 取得長者指定日期的例行行程與完成狀態。 | 長者問：「我今天還要吃什麼藥？」或 AI 需要主動關懷今日行程時。 |
| `complete_routine` | 將特定行程標記為已完成，並記錄生活事件。 | 長者說：「我吃過血壓藥了」或「我剛量完血糖了」。 |
| `create_routine` | 幫長者建立一個新的例行行程或單次提醒。 | 長者說：「幫我記下週一早上九點要看醫生」或「我明天下午要散步」。 |
| `get_recent_events` | 查詢長者近期的生活事件與健康記錄歷史。 | 長者問：「我這週有滑倒過嗎？」或「我昨天晚餐吃了什麼？」。 |
| `get_elder_profile` | 查詢長者的個人暱稱、喜好偏好、健康注意事項與家屬成員。 | 長者問：「你知道我女兒叫什麼名字嗎？」或 AI 主動進行親切對話時。 |
| `remind_pending_routines` | 查詢長者今日尚未完成的待辦行程並回傳提醒事項。 | 長者問：「我還有什麼事情沒做嗎？」或 AI 需要主動進行行程提醒時。 |
| `notify_caregiver` | 發送 AWS SNS 即時緊急警報、例行行程報告或健康摘要至照護者。 | 長者反映跌倒、胸痛、頭暈等緊急狀況，或需推播日報時。 |

---

## 2. 各工具規格與參數架構 (JSON Schema)

### 2.7 `notify_caregiver` (發送照護者通知)
*   **LLM 描述**：`Send immediate SNS alert to the caregiver when the elder experiences emergencies (falls, chest pain, dizziness) or needs routine/summary reports.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "category": {
          "type": "string",
          "enum": ["emergency", "routine", "summary"],
          "description": "通知類別：emergency (跌倒不適緊急警報), routine (行程完成狀態), summary (每日健康摘要)"
        },
        "message": {
          "type": "string",
          "description": "要推播給照護者的詳細訊息內容"
        }
      },
      "required": ["elder_id", "category", "message"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "elder_id": "eld_001",
      "category": "emergency",
      "message_id": "95a12345-6789-0123-4567-890123456789",
      "detail": "已成功發送 emergency 通知給照護者"
    }
    ```

---

## 3. 對話引導與工具調用實例

以下展示 Bedrock Agent 如何在與長者的對話中流暢調用上述工具：

### 實例 A：長者詢問行程 ➔ 調用 `get_today_routines`
*   **長者**：「我今天吃過血壓藥了嗎？」
*   **Agent (Claude)**：*(判定意圖為查詢今日用藥狀態)*
    *   **呼叫工具**：`get_today_routines(elder_id="eld_001", date="2026-07-20")`
    *   **工具回傳**：回傳吃血壓藥 `rtn_001` 的 `status: pending`。
*   **Agent 回覆**：「阿蘭嬤，我看你今天的血壓藥還沒有吃喔！現在已經十點了，要記得趕快吃藥喔。」

### 實例 B：長者口頭完成行程 ➔ 調用 `complete_routine`
*   **長者**：「好啦，我現在把血壓藥吞下去了。」
*   **Agent (Claude)**：*(判定意圖為完成今日用藥)*
    *   **呼叫工具**：`complete_routine(elder_id="eld_001", routine_id="rtn_001", date="2026-07-20", completed_by="conversation")`
    *   **工具回傳**：`{ "status": "success", "routine_id": "rtn_001" }`
*   **Agent 回覆**：「太棒了！有按時吃藥身體才會健健康康。我已經幫你把這個行程記下來了喔。」

### 實例 C：長者新增未來行程 ➔ 調用 `create_routine`
*   **長者**：「明天下午三點，阿明要帶我去看心臟科醫生。」
*   **Agent (Claude)**：*(判定意圖為建立新的單次行程)*
    *   **呼叫工具**：`create_routine(elder_id="eld_001", title="看心臟科", type="other", schedule={"freq": "once", "date": "2026-07-21", "time": "15:00"})`
    *   **工具回傳**：`{ "status": "success", "routine_id": "rtn_003" }`
*   **Agent 回覆**：「阿蘭嬤，我已經幫你記下來了！明天下午三點去看心臟科醫生，到時候我會再提醒你。」

### 實例 D：長者反映跌倒緊急狀況 ➔ 調用 `notify_caregiver`
*   **長者**：「小助手，我剛剛在浴室不小心摔倒了，腳好痛站不起來...」
*   **Agent (Claude)**：*(判定意圖為跌倒極度緊急狀況)*
    *   **呼叫工具**：`notify_caregiver(elder_id="eld_001", category="emergency", message="長者反映在浴室跌倒，腳部劇痛站立困難。")`
    *   **工具回傳**：`{ "status": "success", "category": "emergency", "message_id": "msg_998877" }`
*   **Agent 回覆**：「阿蘭嬤！請您先坐在原地千萬不要急著站起來。我已經立刻發送緊急警報通知志明了，他很快就會關心您！」

---

## 2. 各工具規格與參數架構 (JSON Schema)

為了讓 Bedrock Agent 能夠精準辨識與生成參數，各工具的 API 規格定義如下：

### 2.1 `get_today_routines` (查詢今日行程)
*   **LLM 描述**：`Retrieve a list of scheduled routines and their completion status for a specific elder on a given date.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "date": {
          "type": "string",
          "description": "查詢的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
        }
      },
      "required": ["elder_id", "date"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "date": "2026-07-20",
      "items": [
        {
          "routine_id": "rtn_001",
          "title": "吃血壓藥",
          "type": "medication",
          "scheduled_at": "2026-07-20T09:00:00+08:00",
          "status": "pending"
        },
        {
          "routine_id": "rtn_002",
          "title": "量血壓",
          "type": "other",
          "scheduled_at": "2026-07-20T19:00:00+08:00",
          "status": "done",
          "completed_at": "2026-07-20T09:05:00+08:00"
        }
      ]
    }
    ```

---

### 2.2 `complete_routine` (確認完成行程)
*   **LLM 描述**：`Mark a specific routine as completed and log a life event for the elder.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "routine_id": {
          "type": "string",
          "description": "要完成的行程 ID，例如 rtn_001"
        },
        "date": {
          "type": "string",
          "description": "完成的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
        },
        "completed_by": {
          "type": "string",
          "enum": ["conversation", "elder", "caregiver"],
          "description": "完成此行程的角色，口語回報一律填 conversation"
        }
      },
      "required": ["elder_id", "routine_id", "date", "completed_by"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "message": "Routine rtn_001 marked as done.",
      "routine_id": "rtn_001",
      "completed_at": "2026-07-20T10:15:22+08:00"
    }
    ```

---

### 2.3 `create_routine` (建立新行程)
*   **LLM 描述**：`Create a new scheduled routine (either one-time or recurring) for the elder.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "title": {
          "type": "string",
          "description": "行程的標題或內容，例如：吃血壓藥、看心臟科、跟兒子散步"
        },
        "type": {
          "type": "string",
          "enum": ["diet", "activity", "sleep", "medication", "wellbeing", "other"],
          "description": "行程類型分類"
        },
        "schedule": {
          "type": "object",
          "properties": {
            "freq": {
              "type": "string",
              "enum": ["daily", "weekly", "once"],
              "description": "頻率：每日、每週、單次"
            },
            "date": {
              "type": "string",
              "description": "如果是單次(once)行程，必須提供日期 YYYY-MM-DD；每日或每週則免"
            },
            "time": {
              "type": "string",
              "description": "行程時間，格式為 HH:MM，例如 15:30"
            },
            "weekday": {
              "type": "integer",
              "minimum": 1,
              "maximum": 7,
              "description": "如果是每週(weekly)行程，必須提供星期幾（1=週一，7=週日）"
            }
          },
          "required": ["freq", "time"]
        }
      },
      "required": ["elder_id", "title", "type", "schedule"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "routine_id": "rtn_003",
      "title": "看醫生",
      "scheduled_at": "2026-07-21T15:00:00+08:00"
    }
    ```

---

### 2.4 `get_recent_events` (查詢生活事件歷史)
*   **LLM 描述**：`Retrieve recent life events, activities, and recorded health signals for the elder.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "event_type": {
          "type": "string",
          "description": "可選的事件類型過濾，例如：routine_completion, wellbeing, activity, family, diet, other"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "count": 2,
      "data": [
        {
          "event_id": "evt_001",
          "elder_id": "eld_001",
          "type": "routine_completion",
          "detail": "完成吃血壓藥",
          "ts": "2026-07-20T09:05:00+08:00"
        }
      ]
    }
    ```

---

### 2.5 `get_elder_profile` (查詢長者喜好與個人檔案)
*   **LLM 描述**：`Retrieve personal preferences, hobbies, health notes, and family members of the elder.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "data": {
        "elder_id": "eld_001",
        "name": "林阿蘭",
        "nickname": "阿蘭嬤",
        "health_notes": ["有高血壓歷史", "左膝關節不適"],
        "family": [{"name": "小明", "relation": "兒子"}],
        "preferences": {"tea": "高山烏龍茶", "music": "鄧麗君經典金曲"}
      }
    }
    ```

---

### 2.6 `remind_pending_routines` (主動提醒待辦行程)
*   **LLM 描述**：`Check and retrieve pending scheduled routines for the elder to generate warm reminders.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "長者的唯一識別 ID，例如 eld_001"
        },
        "date": {
          "type": "string",
          "description": "查詢的日期，格式為 YYYY-MM-DD"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **回傳資料 (Output JSON)**：
    ```json
    {
      "status": "success",
      "date": "2026-07-20",
      "pending_count": 1,
      "pending_routines": [
        {
          "routine_id": "rtn_001",
          "title": "吃晚間血壓藥",
          "scheduled_at": "2026-07-20T19:00:00+08:00",
          "status": "pending"
        }
      ]
    }
    ```

---

## 3. 對話引導與工具調用實例

以下展示 Bedrock Agent 如何在與長者的對話中流暢調用上述工具：

### 實例 A：長者詢問行程 ➔ 調用 `get_today_routines`
*   **長者**：「我今天吃過血壓藥了嗎？」
*   **Agent (Claude)**：*(判定意圖為查詢今日用藥狀態)*
    *   **呼叫工具**：`get_today_routines(elder_id="eld_001", date="2026-07-20")`
    *   **工具回傳**：回傳吃血壓藥 `rtn_001` 的 `status: pending`。
*   **Agent 回覆**：「阿蘭嬤，我看你今天的血壓藥還沒有吃喔！現在已經十點了，要記得趕快吃藥喔。」

### 實例 B：長者口頭完成行程 ➔ 調用 `complete_routine`
*   **長者**：「好啦，我現在把血壓藥吞下去了。」
*   **Agent (Claude)**：*(判定意圖為完成今日用藥)*
    *   **呼叫工具**：`complete_routine(elder_id="eld_001", routine_id="rtn_001", date="2026-07-20", completed_by="conversation")`
    *   **工具回傳**：`{ "status": "success", "routine_id": "rtn_001" }`
*   **Agent 回覆**：「太棒了！有按時吃藥身體才會健健康康。我已經幫你把這個行程記下來了喔。」

### 實例 C：長者新增未來行程 ➔ 調用 `create_routine`
*   **長者**：「明天下午三點，阿明要帶我去看心臟科醫生。」
*   **Agent (Claude)**：*(判定意圖為建立新的單次行程)*
    *   **呼叫工具**：`create_routine(elder_id="eld_001", title="看心臟科", type="other", schedule={"freq": "once", "date": "2026-07-21", "time": "15:00"})`
    *   **工具回傳**：`{ "status": "success", "routine_id": "rtn_003" }`
*   **Agent 回覆**：「阿蘭嬤，我已經幫你記下來了！明天下午三點去看心臟科醫生，到時候我會再提醒你。」
