# Bedrock Agent 智慧工具箱 (Action Groups / Tools) 規格說明書

本文件定義供 **Amazon Bedrock Agent (Claude 5)** 自動調用的後端工具（Action Groups / Tools）。當長者在語音對話中提到與「例行公事（用藥、量血壓等）」或「新建行程（約會、看醫生）」相關的意圖時，Agent 會自動選擇並呼叫對應的工具。

所有工具的執行邏輯均透過一個共用的 **Tools Lambda** (或直接在 `chat` 專案中調用) 進行，並直接讀寫 DynamoDB 的 `routines` 與 `events` 表。

---

## 1. 工具總覽與分類清單

為了便於管理與 Agent 辨識，所有工具依據功能劃分為三大類：**行程管理類**、**事件與摘要類**、**安全與警報類**。

### 1.1 行程管理類 (Routine Management)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`get_today_routines`** | 取得長者指定日期的例行行程與完成狀態。 | 詢問今日行程：「我今天還要吃什麼藥？」 |
| **`remind_pending_routines`** | 查詢長者今日尚未完成的待辦行程。 | 詢問待辦或需主動提醒：「我還有什麼事沒做？」 |
| **`complete_routine`** | 將特定行程標記為已完成，並記錄事件。 | 口頭回報完成：「我吃過血壓藥了。」 |
| **`create_routine`** | 幫長者建立一個新的例行行程或單次提醒。 | 新增未來行程：「幫我記下週一早上九點看醫生。」 |
| **`update_routine`** | 更新現有行程的內容（時間、標題、頻率）。 | 更改行程資訊：「把明天散步改到下午四點。」 |
| **`deactivate_routine`** | 停用或取消長者的既有行程。 | 取消行程：「我以後不用再吃那款感冒藥了。」 |

### 1.2 事件與摘要類 (Events & Summaries)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`get_elder_profile`** | 查詢長者暱稱、喜好、健康注意事項與家屬。 | 閒聊或詢問個人資訊：「你知道我女兒叫什麼嗎？」 |
| **`get_recent_events`** | 查詢長者近期的生活事件與健康記錄歷史。 | 查詢近期狀況：「我這週有滑倒過嗎？」 |
| **`get_daily_summaries`**| 查詢長者每日的健康摘要與行程執行率。 | 查詢整體健康趨勢或連續性摘要。 |
| **`get_recent_conversations`**| 查詢長者與 Agent 近期的對話歷史。 | 用於短期記憶恢復或延續先前中斷的話題。 |

### 1.3 安全與警報類 (Safety & Alerts)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`notify_caregiver`** | 發送緊急警報、行程報告或健康摘要至照護者。 | 遭遇緊急狀況：「我剛剛在浴室滑倒了，站不起來。」 |

---

## 2. 各工具規格與系統影響 (Tool Specifications & Impacts)

此章節統整了各工具的 **LLM 描述**、**輸入/輸出 JSON 規格**，以及執行後對資料庫與系統的 **影響範圍 (Impacts)**。

### 2.1 行程管理類 (Routine Management)

#### `get_today_routines` (查詢今日行程)
*   **LLM 描述**：`Retrieve a list of scheduled routines and their completion status for a specific elder on a given date.`
*   **輸入參數**：`elder_id` (字串), `date` (字串，YYYY-MM-DD)
*   **回傳資料**：`{"date": "2026-07-20", "items": [{"routine_id": "rtn_001", "title": "吃血壓藥", "status": "pending", ...}]}`
*   **系統影響**：無副作用 (唯讀)。主要提供大腦當日的行程關懷依據。

#### `remind_pending_routines` (主動提醒待辦行程)
*   **LLM 描述**：`Check and retrieve pending scheduled routines for the elder to generate warm reminders.`
*   **輸入參數**：`elder_id` (字串), `date` (字串，YYYY-MM-DD)
*   **回傳資料**：`{"status": "success", "date": "2026-07-20", "pending_count": 1, "pending_routines": [...]}`
*   **系統影響**：無副作用 (唯讀)。通常觸發於需要主動關懷長者是否忘記吃藥等情境。

#### `complete_routine` (確認完成行程)
*   **LLM 描述**：`Mark a specific routine as completed and log a life event for the elder.`
*   **輸入參數**：`elder_id` (字串), `routine_id` (字串), `date` (字串), `completed_by` (字串，口語回報一律填 `conversation`)
*   **回傳資料**：`{"status": "success", "message": "...", "routine_id": "rtn_001", "completed_at": "..."}`
*   **系統影響**：會寫入 `events` 表（紀錄 type=`routine_completion`）。將導致後續 `get_recent_events` 查詢時出現該完成紀錄，並間接影響健康摘要。

#### `create_routine` (建立新行程)
*   **LLM 描述**：`Create a new scheduled routine (either one-time or recurring) for the elder.`
*   **輸入參數**：`elder_id` (字串), `title` (字串), `type` (enum: diet/activity/sleep/medication/wellbeing/other), `schedule` (物件，包含 freq, date, time, weekday)
*   **回傳資料**：`{"status": "success", "routine_id": "rtn_003", "title": "看醫生", "scheduled_at": "..."}`
*   **系統影響**：寫入 `routines` 表並補齊防呆欄位。直接改變長者未來的行程，App 行事曆上將出現此新增項目。

#### `update_routine` (更新例行行程)
*   **LLM 描述**：`Update an existing scheduled routine (e.g., change time, title, or frequency) for the elder.`
*   **輸入參數**：`elder_id`, `routine_id`, `title`, `type`, `time`, `freq`, `date`, `remind`, `active` (依需求傳入對應更新欄位)
*   **回傳資料**：`{"status": "success", "data": {...}}` (回傳更新後的新版本)
*   **系統影響**：對 `routines` 表進行 Transaction 升版更新 (`is_current` 轉移)。保留舊建檔記錄，未來日期將依照新設定執行。

#### `deactivate_routine` (停用例行行程)
*   **LLM 描述**：`Deactivate or cancel an existing scheduled routine for the elder.`
*   **輸入參數**：`elder_id` (字串), `routine_id` (字串)
*   **回傳資料**：回傳停用後的新版本狀態
*   **系統影響**：透過底層設定 `"active": False`。停用後，未來日子不再出現此行程，但歷史紀錄保留不受影響。

---

### 2.2 事件與摘要類 (Events & Summaries)

#### `get_elder_profile` (查詢長者喜好與個人檔案)
*   **LLM 描述**：`Retrieve personal preferences, hobbies, health notes, and family members of the elder.`
*   **輸入參數**：`elder_id` (字串)
*   **回傳資料**：`{"status": "success", "data": {"name": "林阿蘭", "nickname": "阿蘭嬤", "health_notes": [...], "family": [...], "preferences": {...}}}`
*   **系統影響**：無副作用 (唯讀)。提供 Agent 學習長輩喜好並用於自然對話。

#### `get_recent_events` (查詢生活事件歷史)
*   **LLM 描述**：`Retrieve recent life events, activities, and recorded health signals for the elder.`
*   **輸入參數**：`elder_id` (字串), `event_type` (字串，可選過濾條件)
*   **回傳資料**：`{"status": "success", "count": 2, "data": [{"event_id": "evt_001", "type": "routine_completion", ...}]}`
*   **系統影響**：無副作用 (唯讀)。最多回傳 20 筆近期事件。

#### `get_daily_summaries` (查詢每日健康摘要)
*   **LLM 描述**：`Retrieve recent daily health summaries for the elder.`
*   **輸入參數**：`elder_id` (字串), `days` (整數)
*   **回傳資料**：`{"status": "success", "count": int, "summaries": [...]}`
*   **系統影響**：無副作用 (唯讀)。主要用於協助大腦追蹤長輩的連續性健康趨勢。

#### `get_recent_conversations` (查詢對話紀錄)
*   **LLM 描述**：`Retrieve recent conversation history between the elder and the agent.`
*   **輸入參數**：`elder_id` (字串), `limit` (整數，預設 8，最高 15)
*   **回傳資料**：`{"status": "success", "count": int, "turns": [{"time": "...", "elder": "...", "ai": "..."}, ...]}`
*   **系統影響**：無副作用 (唯讀)。提供斷線或 Session 更換後的短期記憶恢復。

---

### 2.3 安全與警報類 (Safety & Alerts)

#### `notify_caregiver` (發送照護者通知)
*   **LLM 描述**：`Send immediate SNS alert to the caregiver when the elder experiences emergencies (falls, chest pain, dizziness) or needs routine/summary reports.`
*   **輸入參數**：`elder_id` (字串), `category` (字串，enum: emergency/critical_escalation/mitigation/routine/summary), `message` (字串)
*   **回傳資料**：`{"status": "success", "elder_id": "eld_001", "category": "emergency", "message_id": "...", "detail": "..."}`
*   **系統影響**：
    1.  **SNS 推播**：觸發 AWS SNS，實際發送 Email / SMS 給家屬。
    2.  **資料庫寫入**：若為 emergency 類型，會以 canonical key 寫入 `events` 表，產生 `type=safety` 事件。
    3.  **記憶體狀態鎖**：觸發 Lambda 內存 5 分鐘冷卻期鎖定 (`_emergency_state`)，避免同一狀況重複洗版。若在冷卻期內，可能回傳 `"status": "throttled"`。

---


### 2.12 `update_elder_profile` (更新長者個人檔案)
*   **LLM 描述**：`Update the elder's profile, including adding new health notes, appending to lifestyle habits, or changing their nickname based on conversation.`
*   **輸入參數 (Input Parameters)**：
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": { "type": "string" },
        "health_note_to_add": { "type": "string", "description": "欲新增的健康注意事項" },
        "habit_note_to_append": { "type": "string", "description": "欲補充的生活習慣與喜好" },
        "nickname": { "type": "string", "description": "長者希望被稱呼的新暱稱" }
      },
      "required": ["elder_id"]
    }
    ```

## 3. 對話引導與工具調用實例

以下展示 Agent 如何在對話中判斷意圖並流暢調用對應的工具：

### 實例 A：長者詢問行程 ➔ 調用 `get_today_routines`
*   **長者**：「我今天吃過血壓藥了嗎？」
*   **Agent 內部邏輯**：判斷意圖為查詢今日用藥狀態。呼叫 `get_today_routines(elder_id="eld_001", date="2026-07-20")`，系統回傳 `status: pending`。
*   **Agent 回覆**：「阿蘭嬤，我看你今天的血壓藥還沒有吃喔！現在已經十點了，要記得趕快吃藥喔。」

### 實例 B：長者口頭完成行程 ➔ 調用 `complete_routine`
*   **長者**：「好啦，我現在把血壓藥吞下去了。」
*   **Agent 內部邏輯**：判斷意圖為完成今日用藥。呼叫 `complete_routine(elder_id="eld_001", routine_id="rtn_001", date="2026-07-20", completed_by="conversation")`，系統回傳成功。
*   **Agent 回覆**：「太棒了！有按時吃藥身體才會健健康康。我已經幫你把這個行程記下來了喔。」

### 實例 C：長者新增未來行程 ➔ 調用 `create_routine`
*   **長者**：「明天下午三點，阿明要帶我去看心臟科醫生。」
*   **Agent 內部邏輯**：判斷意圖為建立單次行程。呼叫 `create_routine(elder_id="eld_001", title="看心臟科", type="other", schedule={"freq": "once", "date": "2026-07-21", "time": "15:00"})`，系統回傳成功。
*   **Agent 回覆**：「阿蘭嬤，我已經幫你記下來了！明天下午三點去看心臟科醫生，到時候我會再提醒你。」

### 實例 D：長者反映緊急狀況 ➔ 調用 `notify_caregiver`
*   **長者**：「小助手，我剛剛在浴室不小心摔倒了，腳好痛站不起來...」
*   **Agent 內部邏輯**：判斷意圖為緊急醫療與安全狀況。呼叫 `notify_caregiver(elder_id="eld_001", category="emergency", message="長者反映在浴室跌倒，腳部劇痛站立困難。")`，系統發送 SNS 並寫入安全事件。
*   **Agent 回覆**：「阿蘭嬤！請您先坐在原地千萬不要急著站起來。我已經立刻發送緊急警報通知志明了，他很快就會關心您！」
