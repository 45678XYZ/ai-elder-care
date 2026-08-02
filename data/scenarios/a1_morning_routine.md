# 情境 A1：陳阿蘭 — 晨間提醒與天氣查詢

## 覆蓋工具（6）

`remind_pending_routines` · `get_today_routines` · `get_weather_forecast` · `get_elder_profile` · `complete_routine` · `uncomplete_routine`

---

## 前置條件

| 項目 | 值 |
|------|---|
| 長者 | eld_001（陳阿蘭） |
| 時間 | 2026-08-02 08:30 台灣時間 |
| Session | 新 session |
| 已存在 Routine | `rtn_001`：吃血壓藥，daily，08:00，status=pending |
| | `rtn_002`：公園散步，daily，16:00，status=pending |

---

## 對話流程

### Turn 1 — 系統主動問候＋待辦提醒

| 項目 | 內容 |
|------|------|
| 📌 操作 | 系統於 08:30 觸發主動關懷（或長者按麥克風開始對話） |
| 預期工具 | `remind_pending_routines(date="2026-08-02")` |
| AI 回應方向 | 早安問候＋提醒今天還有血壓藥沒吃 |
| 驗證點 | AI 提到「血壓藥」或「吃藥」 |

---

### Turn 2 — 長者問天氣

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「今天**天氣**好不好？我下午想去公園**散步**。」 |
| 觸發詞 | 天氣 |
| 預期工具 | `get_weather_forecast(location=null)` → 系統自動用 address_region「嘉義縣東石鄉」 |
| AI 回應方向 | 報告嘉義天氣（溫度、降雨機率），給出穿衣/帶傘建議 |
| 驗證點 | AI 回覆包含溫度或降雨相關資訊 |
| 備用台詞 | 「嘉義今天會不會下雨？」 |

---

### Turn 3 — 長者問今天還有什麼行程

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「那我**今天還有什麼事**要做？」 |
| 觸發詞 | 今天、什麼事 |
| 預期工具 | `get_today_routines(date="2026-08-02")` |
| AI 回應方向 | 列出今日行程：血壓藥（未完成）、下午四點散步 |
| 驗證點 | AI 提到兩件行程，且血壓藥標示未完成 |
| 備用台詞 | 「我今天有什麼行程？」 |

⤷ IF AI 順便提到散步＋天氣建議 → 理想流程，直接到 Turn 4
⤷ IF AI 只列行程不提散步 → 無影響，繼續 Turn 4

---

### Turn 4 — AI 引用長者習慣（被動觸發）

| 項目 | 內容 |
|------|------|
| 預期工具 | `get_elder_profile()` （AI 為了提供個性化回覆而查詢） |
| AI 回應方向 | 提到「你喜歡去公園散步」或引用 habit_note 的內容 |
| 驗證點 | AI 回覆中出現 habit_note 相關內容 |
| ⚠️ 注意 | 此工具可能在 Turn 2 或 Turn 3 中就被觸發（AI 自主決策），不一定是獨立 turn |

---

### Turn 5 — 長者口頭回報吃藥

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「喔對，**血壓藥**我剛剛已經**吃了**，配溫水吞的。」 |
| 觸發詞 | 血壓藥、吃了 |
| 預期工具 | `complete_routine(routine_id="rtn_001", date="2026-08-02", completed_by="conversation")` |
| AI 回應方向 | 確認記錄、稱讚按時吃藥 |
| 驗證點 | rtn_001 狀態從 pending → completed；App 行程打勾 |
| 備用台詞 | 「我早上八點就把血壓藥吃掉了。」 |

---

### Turn 6 — 長者反悔（觸發 uncomplete）

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「啊等一下，我剛才搞錯了，**還沒吃**啦，那個是昨天的。」 |
| 觸發詞 | 還沒吃 |
| 預期工具 | `uncomplete_routine(routine_id="rtn_001", date="2026-08-02")` |
| AI 回應方向 | 幫忙退回紀錄，溫馨提醒現在去吃 |
| 驗證點 | rtn_001 狀態從 completed → pending；App 行程取消打勾 |
| 備用台詞 | 「不對不對，我那個藥還沒吃，幫我改回來。」 |

---

## 成功標準

- [ ] `remind_pending_routines` 被呼叫且回傳 pending 行程
- [ ] `get_weather_forecast` 被呼叫且回傳嘉義天氣
- [ ] `get_today_routines` 被呼叫且列出 2 項行程
- [ ] `get_elder_profile` 被呼叫（AI 引用個人資訊）
- [ ] `complete_routine` 被呼叫且 rtn_001 → completed
- [ ] `uncomplete_routine` 被呼叫且 rtn_001 → pending
