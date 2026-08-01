# 對話大腦智慧工具箱 (Tools) 規格說明書

本文件定義供**對話大腦**自動調用的後端工具。當長者在語音對話中提到與「例行公事（用藥、量血壓等）」或「新建行程（約會、看醫生）」相關的意圖時，大腦會自動選擇並呼叫對應的工具。

大腦跑在 AgentCore Runtime 上（實作見 `backend/src/agentcore_runtime/`），工具的 LangChain 包裝在該套件的 `tools.py`。所有工具的執行邏輯均透過一個共用的 **Tools Lambda**（`backend/src/handlers/tools.py`）進行，並直接讀寫 DynamoDB 的 `routines` 與 `events` 表。

`elder_id` 不是模型填的參數：它由 Runtime 從 `POST /chat` 的請求 payload 注入後才轉呼叫 Tools Lambda。下方各工具的「輸入參數」仍列出 `elder_id`，因為那是 Tools Lambda 的介面；模型看到的工具 schema 沒有這個欄位。

---

## 1. 工具總覽與分類清單

為了便於管理與大腦辨識，所有工具依據功能劃分為四大類：**行程管理類**、**事件與摘要類**、**安全與警報類**、**衛教知識類**。

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
| **`update_elder_profile`**| 更新長者的健康注意事項、生活習慣或暱稱。 | 發現新的個人特徵或喜好：「我不吃牛肉」、「我最近膝蓋痛」。 |
| **`get_recent_events`** | 查詢長者近期的生活事件與健康記錄歷史。 | 查詢近期狀況：「我這週有滑倒過嗎？」 |
| **`get_daily_summaries`**| 查詢長者每日的健康摘要與行程執行率。 | 查詢整體健康趨勢或連續性摘要。 |
| **`get_recent_conversations`**| 查詢長者與 Agent 近期的對話歷史。 | 用於短期記憶恢復或延續先前中斷的話題。 |

### 1.3 安全與警報類 (Safety & Alerts)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`notify_caregiver`** | 發送緊急警報、行程報告或健康摘要至照護者。 | 遭遇緊急狀況：「我剛剛在浴室滑倒了，站不起來。」 |

### 1.4 衛教知識類 (Health Knowledge)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`search_health_knowledge`** | 檢索衛教與長照知識庫，取得疾病照護、用藥觀念、長照資源的說明。 | 詢問衛教或長照資源：「血壓高平常要注意什麼？」「有沒有人可以幫忙照顧？」 |

### 1.5 環境資訊類 (Environment Info)
| 工具名稱 | 功能描述 (供 LLM 判斷) | 調用契機 (Triggering Intent) |
|---|---|---|
| **`get_weather_forecast`** | 取得長者所在地區的天氣預報（氣溫、天氣狀況、降雨機率）。 | 詢問天氣：「今天天氣怎樣？」「要帶傘嗎？」「今天冷不冷？」，或主動關懷天氣變化提醒 |
| **`get_events_by_time`** | 根據指定日期範圍查詢長者的生活事件歷史。 | 詢問特定時間紀錄：「我上週有按時吃藥嗎？」「前天做了什麼運動？」「這禮拜有跌倒過嗎？」 |

---

## 2. 各工具規格與系統影響 (Tool Specifications & Impacts)

此章節統整了各工具的 **LLM 描述**、**輸入/輸出 JSON 規格**，以及執行後對資料庫與系統的 **影響範圍 (Impacts)**。

### 2.1 行程管理類 (Routine Management)

#### `get_today_routines` (查詢今日行程)
*   **LLM 描述**：`Retrieve a list of scheduled routines and their completion status for the elder on a given date.`
*   **輸入參數**：`elder_id` (字串), `date` (字串，YYYY-MM-DD)
*   **回傳資料**：`{"date": "2026-07-20", "items": [{"routine_id": "rtn_001", "title": "吃血壓藥", "status": "pending", ...}]}`
*   **系統影響**：無副作用 (唯讀)。主要提供大腦當日的行程關懷依據。

#### `remind_pending_routines` (主動提醒待辦行程)
*   **LLM 描述**：`Check and retrieve pending scheduled routines for the elder to generate warm reminders.`
*   **輸入參數**：`elder_id` (字串), `date` (字串，YYYY-MM-DD，選填)
*   **回傳資料**：`{"status": "success", "date": "2026-07-20", "pending_count": 1, "pending_routines": [...]}`
*   **系統影響**：無副作用 (唯讀)。通常觸發於需要主動關懷長者是否忘記吃藥等情境。

#### `complete_routine` (確認完成行程)
*   **LLM 描述**：`Mark a specific routine as completed and log a life event for the elder.`
*   **輸入參數**：`elder_id` (字串), `routine_id` (字串), `date` (字串), `completed_by` (字串，口語回報一律填 `conversation`)
*   **回傳資料**：`{"status": "success", "data": {...}}`
*   **系統影響**：會寫入 `events` 表（紀錄 type=`routine_completion`）。將導致後續 `get_recent_events` 查詢時出現該完成紀錄，並間接影響健康摘要。

#### `create_routine` (建立新行程)
*   **LLM 描述**：`Create a new scheduled routine (either one-time or recurring) for the elder.`
*   **輸入參數**：`elder_id` (字串), `title` (字串), `type` (enum: medication/diet/activity/wellbeing/other), `time` (字串，HH:MM), `freq` (enum: daily/weekly/once), `date` (字串，YYYY-MM-DD，僅單次行程需提供)
*   **回傳資料**：`{"status": "success", "data": {...}}` (回傳新建的行程紀錄)
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
*   **回傳資料**：`{"status": "success", "data": {"name": "林阿蘭", "nickname": "阿蘭嬤", "health_notes": [...], "family": [...], "habit_note": "..."}}`
*   **系統影響**：無副作用 (唯讀)。提供 Agent 學習長輩喜好並用於自然對話。

#### `update_elder_profile` (更新長者個人檔案)
*   **LLM 描述**：`Update the elder's profile, including adding new health notes, appending to lifestyle habits, changing their nickname, or switching language preference based on conversation. Only set lang_preference/hakka_dialect when the elder EXPLICITLY asks to switch.`
*   **輸入參數**：`elder_id` (字串), `health_note_to_add` (字串，欲新增的健康注意事項), `habit_note_to_append` (字串，欲補充的生活習慣與喜好), `nickname` (字串，新暱稱), `lang_preference` (字串，選填：zh-TW 或 hak), `hakka_dialect` (字串，選填：htia_sixian / htia_hailu / htia_dapu / htia_raoping / htia_zhaoan / htia_nansixian)
*   **回傳資料**：`{"status": "success", "message": "已成功更新長者個人檔案", "updated_fields": [...], "data": {...}}`
*   **語言切換**：`lang_preference` 與 `hakka_dialect` 僅在長者**明確要求**切換語言時才填入。無效值會被靜默忽略，不中斷對話。此路徑與 `PATCH /elders` 的 REST API 路徑並行：前者供對話中切換，後者供前端 UI 操作。
*   **健康註記的寫入方式**：`health_note_to_add` 會以 `source: "agent"` 原子 append 進 `health_notes`（`db.append_health_note`），**不做讀出再整份寫回**。同一個欄位照護者也會在 App 上增刪，整份覆寫會讓其中一邊的結果無聲消失。已存在的相同內容不重複加入。
*   **來源標示**：由此工具寫入的註記在 API 上帶 `source: "agent"`，與照護者手填的 `caregiver` 分開，讓照護者看得出哪幾筆是 AI 從談話裡聽來的（契約見 `docs/api.md` 的 health_notes 物件）。
*   **回傳的 `health_notes`**：攤平成純文字陣列，不含 `note_id` 等內部識別碼。

#### `get_recent_events` (查詢生活事件歷史)
*   **LLM 描述**：`Retrieve recent life events, activities, and recorded health signals for the elder.`
*   **輸入參數**：`elder_id` (字串), `event_type` (字串，可選過濾條件)
*   **回傳資料**：`{"status": "success", "count": 2, "data": [{"event_id": "evt_001", "type": "routine_completion", ...}]}`
*   **系統影響**：無副作用 (唯讀)。最多回傳 20 筆近期事件。

#### `get_daily_summaries` (查詢每日健康摘要)
*   **LLM 描述**：`Retrieve recent daily health summaries for the elder to understand health trends over multiple days. Use this when the elder or caregiver asks about recent health status, trends, or when you need context about the elder's health over the past few days.`
*   **輸入參數**：`elder_id` (字串), `days` (整數，預設為 3)
*   **回傳資料**：`{"status": "success", "count": int, "summaries": [...]}`
*   **系統影響**：無副作用 (唯讀)。主要用於協助大腦追蹤長輩的連續性健康趨勢。

#### `get_recent_conversations` (查詢對話紀錄)
*   **LLM 描述**：`Retrieve the most recent conversation turns with the elder. Use this tool when you feel you have lost context of the current conversation, for example after a session timeout, to recall what was discussed earlier in this session.`
*   **輸入參數**：`elder_id` (字串), `limit` (整數，預設 8，最高 15)
*   **回傳資料**：`{"status": "success", "count": int, "turns": [{"time": "...", "elder": "...", "ai": "..."}, ...]}`
*   **系統影響**：無副作用 (唯讀)。提供斷線或 Session 更換後的短期記憶恢復。

---

### 2.3 安全與警報類 (Safety & Alerts)

#### `notify_caregiver` (發送照護者通知)
*   **LLM 描述**：
    `Send SNS notification to caregiver. Use category to control safety behavior:`
    `- emergency: First-time urgent alert (fall/chest pain/cannot move). Has 5-min cooldown. Writes DB event.`
    `- critical_escalation: Condition worsening (new bleeding/fainting/severe pain). BYPASSES cooldown. Use when elder reports new severe symptoms after initial emergency.`
    `- mitigation: Elder verbally says they feel better. Sets status to WARNING (pending caregiver confirmation). Does NOT resolve the alert. Requires active emergency to exist.`
    `- routine: Scheduled task completion digest.`
    `- summary: Daily health summary report.`
    `IMPORTANT: Only caregivers (not elders) can fully resolve an alert via the App.`
*   **輸入參數**：`elder_id` (字串), `category` (字串，enum: emergency/critical_escalation/mitigation/routine/summary), `message` (字串), `context_event_id` (字串，選填，用於 escalation/mitigation 帶入對應的 alert_id), `rag_content` (字串，選填，用於補充衛教指引)
*   **回傳資料**：`{"status": "success", "elder_id": "eld_001", "category": "emergency", "message_id": "...", "detail": "..."}`
*   **系統影響**：
    1.  **SNS 推播**：觸發 AWS SNS，實際發送 Email / SMS 給家屬。
    2.  **資料庫寫入**：若為 emergency 類型，會以 canonical key (`SAFETY#{alert_id}`) 寫入 `events` 表，產生 `type=safety` 事件。若是 critical_escalation 或 mitigation，若帶入 `context_event_id` 則會冪等收斂到同一筆安全事件。
    3.  **記憶體狀態鎖**：觸發 Lambda 內存 5 分鐘冷卻期鎖定 (`_emergency_state`)，避免同一狀況重複洗版。若在冷卻期內，可能回傳 `"status": "throttled"`。critical_escalation 可繞過冷卻期。

---

### 2.4 衛教知識類 (Health Knowledge)

#### `search_health_knowledge` (檢索衛教知識庫)
*   **LLM 描述**：`Search the health education knowledge base for elder care guidance: chronic disease care (hypertension, diabetes, stroke, asthma, COPD, osteoporosis, metabolic syndrome), dementia, fall prevention, assistive devices, oral care, nutrition, medication concepts, seasonal health, and long-term care services in Taiwan (respite care, transportation, home care, subsidy applications). Use the elder's own wording as the query.`
*   **輸入參數**：`query` (字串，用長者的原話即可)
*   **回傳資料**：`{"status": "success", "count": int, "passages": ["...", ...]}`
*   **系統影響**：無副作用 (唯讀)。取代原本掛在託管 Agent 上的 knowledge base association；知識庫本體見 `terraform/bedrock_kb.tf`，回傳段落數由 `agent_kb_top_k` 控制。
*   **何時該檢索**：判斷準則寫在 `backend/src/agentcore_runtime/prompts.py` 的系統提示，不寫在工具描述——閒聊與查詢長者自身紀錄時不應檢索。

---

### 2.5 環境資訊類 (Environment Info)

#### `get_weather_forecast` (取得天氣預報)
*   **LLM 描述**：`Get the current weather forecast for the elder's area. Use when the elder asks about weather, temperature, rain, or whether to bring an umbrella/wear warm clothes. Also useful for proactive care reminders related to weather (e.g., cold snap warning, heat stroke prevention).`
*   **輸入參數**：`elder_id` (字串), `location` (字串，選填，氣象署地區名稱如「臺北市」「高雄市」，不填則從長者居住地自動取得)
*   **回傳資料**：`{"status": "success", "location": "臺北市", "forecast": [{"start_time": "...", "end_time": "...", "weather": "多雲短暫雨", "temp_low": 24, "temp_high": 28, "rain_prob": 70}]}`
*   **系統影響**：無副作用 (唯讀)。呼叫中央氣象署 Open Data API（F-C0032-001 一般天氣預報），回傳未來 36 小時分三時段的天氣、氣溫與降雨機率。

#### `get_events_by_time` (依時間範圍查詢事件)
*   **LLM 描述**：`Query the elder's life events within a specific date range. Use when the elder asks about what happened on particular days. Unlike get_recent_events which returns the latest 20, this tool filters by exact dates.`
*   **輸入參數**：`elder_id` (字串), `start_date` (字串，YYYY-MM-DD), `end_date` (字串，YYYY-MM-DD), `event_type` (字串，選填：routine_completion / wellbeing / activity / family / diet / safety / other)
*   **回傳資料**：`{"status": "success", "count": 5, "period": {"start": "2026-07-25", "end": "2026-07-28"}, "data": [...]}`
*   **系統影響**：無副作用 (唯讀)。直接查詢 DynamoDB events 表的 `events-by-time` GSI，以 `event_time_key` 做範圍查詢。最多回傳 50 筆。

---

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
*   **Agent 內部邏輯**：判斷意圖為建立單次行程。呼叫 `create_routine(elder_id="eld_001", title="看心臟科", type="other", time="15:00", freq="once", date="2026-07-21")`，系統回傳成功。
*   **Agent 回覆**：「阿蘭嬤，我已經幫你記下來了！明天下午三點去看心臟科醫生，到時候我會再提醒你。」

### 實例 D：長者反映緊急狀況 ➔ 調用 `notify_caregiver`
*   **長者**：「小助手，我剛剛在浴室不小心摔倒了，腳好痛站不起來...」
*   **Agent 內部邏輯**：判斷意圖為緊急醫療與安全狀況。呼叫 `notify_caregiver(elder_id="eld_001", category="emergency", message="長者反映在浴室跌倒，腳部劇痛站立困難。")`，系統發送 SNS 並寫入安全事件。
*   **Agent 回覆**：「阿蘭嬤！請您先坐在原地千萬不要急著站起來。我已經立刻發送緊急警報通知志明了，他很快就會關心您！」

### 實例 E：長者詢問天氣 ➔ 調用 `get_weather_forecast`
*   **長者**：「今天要不要帶雨傘出門？」
*   **Agent 內部邏輯**：判斷意圖為詢問天氣。呼叫 `get_weather_forecast(elder_id="eld_001")`，系統回傳降雨機率 70%、多雲短暫雨。
*   **Agent 回覆**：「阿蘭嬤，今天有七成的機會會下雨喔，天氣預報說會有短暫陣雨。出門記得帶把傘比較安心！」

### 實例 F：長者詢問過去紀錄 ➔ 調用 `get_events_by_time`
*   **長者**：「我上禮拜有吃藥嗎？」
*   **Agent 內部邏輯**：判斷意圖為查詢過去一週用藥紀錄。呼叫 `get_events_by_time(elder_id="eld_001", start_date="2026-07-21", end_date="2026-07-27", event_type="routine_completion")`，系統回傳 5 筆完成紀錄。
*   **Agent 回覆**：「阿蘭嬤，上禮拜七天裡面你有五天都有按時吃藥，很棒喔！只有禮拜三跟禮拜六沒有紀錄，下次要記得喔。」
