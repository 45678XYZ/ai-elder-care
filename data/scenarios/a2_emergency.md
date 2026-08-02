# 情境 A2：陳阿蘭 — 跌倒緊急通報與升級

## 覆蓋工具（5，notify_caregiver ×3 類別）

`notify_caregiver(emergency)` · `notify_caregiver(critical_escalation)` · `notify_caregiver(mitigation)` · `search_health_knowledge` · `get_recent_events` · `update_elder_profile`

---

## 前置條件

| 項目 | 值 |
|------|---|
| 長者 | eld_001（陳阿蘭） |
| 時間 | 2026-08-02 14:00 台灣時間 |
| Session | 新 session（與 A1 不同 session） |
| 已存在事件 | 上週有一筆 type=safety 事件（7/28 浴室差點滑倒） |

---

## 對話流程

### Turn 1 — 長者呼救：跌倒

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「小安，我剛剛在**浴室滑倒**了，**膝蓋**好痛，**站不起來**……」 |
| 觸發詞 | 浴室、滑倒、站不起來 |
| 預期工具 | `notify_caregiver(category="emergency", message="長者反映在浴室滑倒，膝蓋疼痛且無法站立。")` |
| AI 回應方向 | 安撫長者待在原地不要動、已通知志明 |
| 驗證點 | SNS 通知發出；events 表新增 type=safety 事件；AI 說「已通知」 |
| 備用台詞 | 「我摔倒了，腳好痛站不起來。」 |

---

### Turn 2 — AI 查衛教知識（跌倒急救）

| 項目 | 內容 |
|------|------|
| 預期工具 | `search_health_knowledge(query="跌倒後急救處理")` |
| AI 回應方向 | 給出跌倒後的安全指引（不要急著站、檢查有無流血、保持平躺等） |
| 驗證點 | AI 回覆包含具體急救步驟（非泛泛安撫） |
| ⚠️ 注意 | 此工具可能和 Turn 1 的 notify 在同一輪被呼叫 |

---

### Turn 3 — 狀況惡化：頭暈加重

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「我**頭越來越暈**了，眼前**黑黑的**，好像快要**暈過去**……」 |
| 觸發詞 | 頭暈、暈過去 |
| 預期工具 | `notify_caregiver(category="critical_escalation", message="長者狀況惡化：頭暈加劇、視線模糊、接近昏厥。", context_event_id="<Turn 1 回傳的 alert_id>")` |
| AI 回應方向 | 加強安撫、請長者平躺、告知已再次通知家屬情況更嚴重 |
| 驗證點 | 第二次通知發出（繞過 5 分鐘冷卻期）；事件收斂到同一筆 safety record |
| 備用台詞 | 「我現在頭很暈，快要昏倒了。」 |

---

### Turn 4 — AI 查近期安全事件（模式判斷）

| 項目 | 內容 |
|------|------|
| 預期工具 | `get_recent_events(event_type="safety")` |
| AI 回應方向 | AI 可能提到「上次也有在浴室差點滑倒的紀錄」，建議加裝止滑墊 |
| 驗證點 | 回傳結果包含上週 7/28 的 safety 事件 |
| ⚠️ 注意 | 此工具由 AI 自主判斷呼叫，若未觸發不影響主流程 |

---

### Turn 5 — 長者說好一些了

| 項目 | 內容 |
|------|------|
| 長者（必說） | 「我現在有**好一點**了，頭沒那麼暈了，志明打電話來了。」 |
| 觸發詞 | 好一點 |
| 預期工具 | `notify_caregiver(category="mitigation", message="長者表示頭暈已緩解，兒子已電話聯繫。", context_event_id="<alert_id>")` |
| AI 回應方向 | 提醒繼續坐著休息、等志明確認，告知已更新狀態 |
| 驗證點 | 安全事件狀態更新為 WARNING（待照護者確認解除） |
| 備用台詞 | 「現在比較好了，不那麼暈了。」 |

---

### Turn 6 — AI 更新長者 profile

| 項目 | 內容 |
|------|------|
| 預期工具 | `update_elder_profile(health_note_to_add="近期在浴室跌倒後出現頭暈，有短暫昏厥感")` |
| AI 回應方向 | 不一定會明說更新了 profile，可能在安撫中順帶完成 |
| 驗證點 | health_notes 新增一筆 source="agent" 的紀錄 |
| ⚠️ 注意 | 此工具可能在 Turn 5 同輪呼叫，或由 AI 在對話收尾時自主觸發 |

---

## 成功標準

- [ ] `notify_caregiver(emergency)` 發出第一次緊急通知
- [ ] `search_health_knowledge` 檢索到跌倒相關衛教內容
- [ ] `notify_caregiver(critical_escalation)` 繞過冷卻期發出第二次通知
- [ ] `get_recent_events` 查到上週的 safety 事件（驗證模式判斷）
- [ ] `notify_caregiver(mitigation)` 更新事件狀態為 WARNING
- [ ] `update_elder_profile` 新增健康註記（source=agent）
