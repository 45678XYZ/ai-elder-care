# API 規格

所有端點掛在 API Gateway，路徑前綴 `/v1`。本文件是前後端唯一契約。

## 共通慣例

- **認證**：所有請求帶 `Authorization: Bearer <Cognito ID Token>`。長者 token 帶 `elder_id`，只能存取自己；照護者只能存取已綁定長者。
- **呼叫者身分取自 token**，不以 `caregiver_id` 參數指定呼叫者。綁定關係存於 `elders.caregiver_ids`，只能由 `POST /elders`（建立者自動綁定）與 `POST /elders/{id}/caregivers`（見「綁定照護者」）寫入；一般越權回 403，close 與綁定 endpoint 依其防洩漏規則回 404。
- **格式**：request/response 為 `application/json; charset=utf-8`。
- **時間**：ISO 8601 含時區，如 `2026-07-14T09:05:00+08:00`；日期為 `YYYY-MM-DD`；日界以台灣時間（+08:00）為準。
- **ID 格式／前綴**：長者 ID 為 `eld_` 後接 12 個小寫十六進位字元；其餘前綴為 `rtn_`（例行公事）、`evt_`（事件）、`cnv_`（turn）、`ses_`（session）、`cg_`（照護者對外識別，見「綁定照護者」）、`hn_`（健康註記單筆，見「長者資料」）。batch chunk ID 僅供後端追蹤，不由 API 回傳。
- **分頁**：列表支援 `?limit=`（預設 50）與 `?next_token=`。資料超過一頁時 response 最外層帶 `next_token`；下一次請求必須原樣帶回。它是後端由資料庫游標編碼的不透明字串，前端不得解析；沒有此欄位表示已無下一頁。
- **Hybrid 處理**：`POST /chat` 透過對話大腦的 tool calling 同步處理 routine 變更與 safety event，不等待 session batch。Session 關閉採雙管道：App 可主動呼叫 close endpoint 即時關閉，EventBridge 週期性收斂（idle close）則確保未明確關閉的 session 最終仍會收斂。

### 共用 enum

| 名稱 | 值 |
|---|---|
| `SessionState` | `active` \| `closing` \| `closed` |
| `SessionBatchStatus` | `pending` \| `processing` \| `completed` \| `failed` |
| `SummaryDataStatus` | `complete` \| `partial` |
| `Gender` | `male` \| `female` \| `other` |
| `Language` | `zh-TW` \| `hak` |
| `EventType` | `diet` \| `activity` \| `sleep` \| `medication` \| `wellbeing` \| `safety` \| `other` |
| `RoutineStatus` | `pending` \| `done` \| `missed` |
| `CompletedBy` | `conversation` \| `elder` \| `caregiver` |

Session 只允許 `active→closing→closed`；`closed` 不再接受新 turn。

### 錯誤格式

非 2xx 一律為下列結構。

```json
{ "error": { "code": "ELDER_NOT_FOUND", "message": "找不到指定的長者" } }
```

| HTTP | 使用時機與 `code` |
|---|---|
| 400 | 缺漏／格式錯誤（`INVALID_PARAMETER`）；音訊超長（`AUDIO_TOO_LONG`）；指定日期無該 routine（`ROUTINE_NOT_SCHEDULED`） |
| 401 | token 缺漏或無效（`UNAUTHORIZED`） |
| 403 | 越權（`FORBIDDEN`）；不適用於 close endpoint 的 session 存在性／ownership 判斷 |
| 404 | `ELDER_NOT_FOUND`、`ROUTINE_NOT_FOUND`、`SESSION_NOT_FOUND`、`CAREGIVER_NOT_FOUND`、`HEALTH_NOTE_NOT_FOUND`；close endpoint 對不存在或不屬該長者的 session 都使用 `SESSION_NOT_FOUND` |
| 409 | `REQUEST_IN_PROGRESS`、`IDEMPOTENCY_CONFLICT` |
| 429 | 超過 stage 節流上限（`THROTTLED`）；前端退避重試 |
| 500 | `INTERNAL_ERROR` |

`code` 是前端 UX 分支的穩定識別碼；程式不得依賴可能調整的 `message`。任一端點可能回通用錯誤；端點特例另行註明。

請求在抵達 handler 前被 API Gateway 擋下時（token 無效、路由不存在、payload 過大、節流、integration 逾時），`code` 為該 gateway 錯誤類型，如 `UNAUTHORIZED`、`MISSING_AUTHENTICATION_TOKEN`、`REQUEST_TOO_LARGE`、`THROTTLED`、`INTEGRATION_TIMEOUT`，`message` 為英文原文。

---

## 對話與 Session

### POST /chat — realtime 對話快路徑

長者輸入一句話並取得 AI 回覆。`text` 與 `audio` 擇一必填。每次新的長者輸入產生新的 `client_request_id`；同一次輸入重送沿用原值。

response 前只執行：

1. 回覆所需的 ASR、近期上下文、既有 events/routines 查詢、AgentCore 長期記憶與 AI/TTS。
2. 透過 Bedrock Agent tool calling 同步處理 routine 變更與 safety event 寫入。
3. 需要立即生效的 routine 建立、修改、刪除或完成，以及潛在高風險 safety event 的事件寫入。


一般生活 events 不由 realtime materialize；只在 session close 後由 batch 處理。後端 extraction 狀態不回傳 App。

#### Request

第一輪省略 `session_id`：

```json
{
  "client_request_id": "ad381d1e-2b96-4dc2-83ac-c58ab0b934db",
  "elder_id": "eld_a1b2c3d4e5f6",
  "lang": "zh-TW",
  "text": "我吃過血壓藥了，明天下午三點小明要帶我去看醫生"
}
```

後續帶回前一輪 response 的 `session_id`；audio 範例：

```json
{
  "client_request_id": "5dc66af8-ed50-4e1e-81f7-52f84cc4348e",
  "session_id": "ses_01J8...",
  "elder_id": "eld_a1b2c3d4e5f6",
  "lang": "hak",
  "audio": { "data": "<base64>", "format": "m4a" }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `client_request_id` | string | 必填，UUID；重送沿用 |
| `session_id` | string | 選填；第一輪省略 |
| `elder_id` | string | 必填 |
| `lang` | string | 必填，`zh-TW` \| `hak` |
| `text` | string | 與 `audio` 擇一；裝置端辨識結果或使用者輸入文字 |
| `audio.data` | string | base64；單句 ≤ 60 秒，否則 400 `AUDIO_TOO_LONG` |
| `audio.format` | string | `m4a` \| `wav` |

`lang` 是本輪 Agent、ASR 與 TTS 的明確語言，不從輸入文字自動偵測。`lang=hak` 時，
後端只讀 elder profile 的 `hakka_dialect`，App 不在 `/chat` 傳腔調；新 turn reserve 時保存
腔調快照，ASR 與 TTS 共用。

後端 routing 順序固定先做 turn 冪等判定，再做任何 Session 選擇、建立或 inflight reserve：

1. 先以 `elder_id` scope + `client_request_id` 查 existing turn，並比對正規化 `request_hash`。同 scope/ID 但 hash 不同回 409 `IDEMPOTENCY_CONFLICT`。
2. 相同 hash 的 existing turn 為 `completed`／`failed` 時直接 replay 原 terminal 結果；`completed` 即使原 session 已為 `closing`／`closed` 仍回原 200 業務結果與原 `session_id`，只重新簽發音訊 URL，不建立新 session 或 reserve。`processing` lease 尚有效時回 409 `REQUEST_IN_PROGRESS`；lease expired 時才可條件式接管原 turn、原 session 與既有 reservation，不重新執行 Session routing 或 reserve。
3. 只有查無 existing turn 的全新 `client_request_id` 才執行下列 Session 選擇，必要時建立新 `active` session，並 reserve 新 turn。

全新 ID 的 Session 選擇規則：

- 未帶 `session_id`：建立新的 `active` session。
- session 存在、屬於該長者、仍 `active`、未超過 idle 門檻且未達 turn/input bytes 上限：沿用。
- session 屬於該長者但已 idle、`closing`、`closed` 或達上限：該 turn 不寫入原 session；後端建立新的 active session並在 response 回新 `session_id`。idle／達上限的原 session 由 closer 收斂。
- session 不存在或不屬於該長者：一律 404 `SESSION_NOT_FOUND`，與 close 一致不以 403 區分，避免洩漏 session 是否存在。

後端只對查無 existing turn 的全新 ID，在 session 仍為 active 時以 transaction 建立 processing turn lease並 reserve inflight ID；turn 與 session inflight 均受上限約束。reserve 與 close 競爭時，reserve 先成功則 close 回 409 等待收斂；close 的 `active→closing` 先成功則本 turn 不得 append 原 session，必須建立新 active session後 reserve。final success 以單一 DynamoDB transaction 原子提交 completed 穩定結果、所有 realtime routine/event mutations、移除 inflight、按接納順序追加 turn/context IDs及更新 counts/activity；每 turn action 數受限，transaction 不超過 100 items。

相同 ID/hash 的 processing lease 尚有效時回 409 `REQUEST_IN_PROGRESS`，到期時才可接管；相同 ID 搭配不同 payload/request hash 回 409 `IDEMPOTENCY_CONFLICT`。completed 的相同請求依前述前置 routing 回原 200 業務結果，不重複寫 event 或 routine，亦不受原 session 後續 closing/closed 影響。terminal failed 的 turn 已移除 session inflight reservation，且保證沒有 routine/event side effects；相同 ID/hash 重播首次持久化的穩定錯誤，要重新嘗試可使用新的 `client_request_id`。但 realtime business commit 一旦成功，turn 一定為 completed；其後即使 HTTP／API Gateway 傳輸失敗也不得改成 failed，App 必須先以原 ID replay completed 結果，不可改用新 ID，以免重複副作用。

#### Response 200

`/chat` 維持 flat response：

```json
{
  "conversation_id": "cnv_8f25b1...",
  "session_id": "ses_01J8NEW...",
  "transcript": "我吃過血壓藥了，明天下午三點小明要帶我去看醫生",
  "reply_text": "有按時吃藥真棒！明天下午三點小明帶你去看醫生，我幫你記下來了，明天會提醒你。",
  "reply_audio_url": "https://<s3-presigned-url>",
  "reply_audio_status": "pending",
  "routines_updated": true
}
```

| 欄位 | 說明 |
|---|---|
| `conversation_id` | 本 turn ID；相同 `client_request_id` 重送回同一 ID |
| `session_id` | 本 turn 首次接納時實際使用的 session；相同 `client_request_id` replay 一律回原 ID，即使該 session 後續已 closing/closed。只有全新 ID 在指定原 session idle、closing、closed 或達上限時才會取得新 ID |
| `transcript` | audio 的 ASR 結果；text 則原樣回傳 |
| `reply_text` | AI 回覆 |
| `reply_audio_url` | 15 分鐘 S3 presigned URL；`reply_audio_status` 為 `unavailable` 或簽發失敗時為 `null`，但文字 turn 仍 completed |
| `reply_audio_status` | `pending`｜`ready`｜`unavailable`，見下方說明 |
| `routines_updated` | 本次 response 前已成功建立、修改、停用 routine 或完成 occurrence 時為 true，否則為 false |

語音合成是非同步的。自建 TTS 模型合成一段回覆要數十秒到數分鐘，而本 API 走 API Gateway
REST、整條請求上限 29 秒，因此 `/chat` 只回文字並把合成工作入列，音訊稍後才寫進
`reply_audio_url` 指向的位置。

| `reply_audio_status` | 意義 | App 的行為 |
|---|---|---|
| `pending` | 合成已入列，音訊尚未就緒 | URL 目前會回 404；在 15 分鐘效期內重試，取得 200 後播放 |
| `ready` | 音訊已存在 | 直接播放 |
| `unavailable` | 本輪不會有音訊（無可用 provider、語言不支援或入列失敗） | 只呈現文字，不必等待 |

以相同 `client_request_id` 重送時，狀態同樣反映當下：合成還沒完成回 `pending`，完成後
回 `ready`。turn 只在音訊真的寫入 S3 之後才保存 object key，因此 `ready` 保證可播放。

`routines_updated` 必須反映已提交的業務結果，不得只代表模型曾提出候選。App 收到 true 後可背景呼叫 `GET /routines` 更新定義與當日狀態。一般 events 尚未產生不影響 200 response；回覆失敗時沿用通用錯誤格式。

### POST /chat/sessions/{session_id}/close — 明確關閉

App 在停止免手持互動、離開對話畫面或切換長者前呼叫。此 endpoint 表示停止向該 session 追加 turn、freeze immutable snapshot，並啟動離線 normal events materialization；不保證 response 時 batch 已完成。

> Session 關閉採雙管道設計：前端可主動呼叫此 endpoint 即時關閉（適用於即時展示等需要快速收斂的場景），EventBridge 週期性收斂（idle close）則確保未明確關閉的 session 最終仍會收斂。兩者邏輯與條件式寫入完全一致。

只有 token 對應的長者本人可呼叫。request body 可省略；傳送時必須是空 object：

```json
{}
```

path session 不存在或不屬於 token 中的長者，一律回 404 `SESSION_NOT_FOUND`，不以 403 區分，避免洩漏 session 是否存在。

#### Response 200

```json
{
  "session_id": "ses_01J8...",
  "status": "closed",
  "closed_at": "2026-07-14T09:20:00+08:00",
  "batch_status": "pending"
}
```

| 欄位 | 說明 |
|---|---|
| `session_id` | 已關閉 session ID |
| `status` | 固定 `closed` |
| `closed_at` | snapshot 驗證完成、session 進入 closed 的時間；重送不改變 |
| `batch_status` | 目前 `pending` \| `processing` \| `completed` \| `failed` |

close 以 session 狀態保證冪等，不需要 `client_request_id`：

- `active` 且沒有 inflight turn 時，首次呼叫完成 `active→closing→closed`、freeze ordered completed turns、驗證 snapshot 並設定 batch pending後回 200。
- `active` 仍有 inflight turn，或已為 `closing` 但尚未收斂時，回 409 `REQUEST_IN_PROGRESS`。App 不產生 client ID，只重試同一 close call；後端 EventBridge closer 仍會接管 lease-expired processing turn繼續處理，或安全 terminalize failed 並移除 reservation。非 terminal turn或 inflight 尚未清空時不得 freeze／closed。
- 同一 session 已 `closed` 時，重複 close 回相同 `closed_at` 與當下 `batch_status` 的 200。
- `/chat` 與 close race 由條件式 session transition 決定：turn reserve 先成功時 close 看到 inflight 並回 409；close 先轉為 closing 時 chat 不可 append 舊 session，須建立新 active session後 reserve。
- `closed` 在 batch 前已成立；SQS 傳送失敗可由 recovery sweep 重投，不會把 session reopen。
- response 只承諾 session 不再接受新 turn且離線工作已可恢復地啟動，不承諾 normal events 已 materialized。

---

## 長者資料

公開欄位 enum：`gender` 只接受 `male|female|other`；`lang_preference` 只接受 `zh-TW|hak`；
`hakka_dialect` 只接受 `htia_sixian|htia_hailu|htia_dapu|htia_raoping|htia_zhaoan|htia_nansixian`。
其他值回 400 `INVALID_PARAMETER`。客語腔調是 ASR/TTS 唯一來源，預設 `htia_sixian`。

### GET /elders — 列表

照護者取得 `caregiver_ids` 含其 token `sub` 的所有綁定長者；長者帳號只回 token `elder_id` 對應的自己一筆。結果套用共通分頁規則。

```json
{
  "items": [
    {
      "elder_id": "eld_a1b2c3d4e5f6",
      "name": "陳阿蘭",
      "nickname": "阿蘭嬤",
      "birth_year": 1948,
      "gender": "female",
      "lang_preference": "zh-TW",
      "hakka_dialect": "htia_sixian",
      "address_region": "台北市大安區",
      "health_notes": [
        { "note_id": "hn_9c1f2a4b7d3e", "text": "高血壓", "source": "caregiver", "created_at": "2026-07-01T10:00:00+08:00" },
        { "note_id": "hn_4e8a0b6c2f19", "text": "最近膝蓋痛", "source": "agent", "created_at": "2026-07-30T20:11:00+08:00" }
      ],
      "family": [
        { "relation": "兒子", "name": "陳志明", "note": "在台北工作，每週三來訪" },
        { "relation": "孫子", "name": "小明", "note": "高中生" }
      ],
      "habit_note": "早睡早起，喜歡去公園散步、看歌仔戲",
      "created_at": "2026-07-01T10:00:00+08:00",
      "updated_at": "2026-07-01T10:00:00+08:00"
    }
  ]
}
```

#### health_notes 物件

| 欄位 | 說明 |
|---|---|
| `note_id` | 單筆識別碼，`hn_` 後接 12 個小寫十六進位字元。由後端產生，刪除單筆時以此指定 |
| `text` | 註記內容 |
| `source` | `caregiver`（照護者在 App 填的）｜`agent`（對話中由 `update_elder_profile` 工具依長輩談話補上的） |
| `created_at` | 加入時間 |

`source` 是**必要資訊而非裝飾**：同一個欄位被照護者與對話大腦共寫，AI 聽來的那幾筆更可能出錯、也更需要照護者確認，前端必須分得出來才做得到。

寫入時 `note_id`、`created_at` 一律由後端補；`source` 只由後端依寫入路徑決定，client 指定會被拒絕。相容性：`health_notes` 送純字串陣列仍會被接受，一律視為 `source: "caregiver"`，回應一律是物件陣列。

### GET /elders/{elder_id} — 單筆

回傳同上單一物件。長者只能讀自己；照護者只能讀 `caregiver_ids` 包含其 token `sub` 的長者，其他情況依共通授權錯誤處理。

### POST /elders — 建立（照護者）

Request 是上述物件去掉 `elder_id`、`caregiver_ids`、`created_at`、`updated_at`；`name` 必填，`lang_preference` 預設 `zh-TW`，`hakka_dialect` 預設 `htia_sixian`，未提供或為空的 `health_notes`、`family` 由後端補 `[]`。`elder_id` 由後端以 `"eld_" + uuid4().hex[:12]` 產生；建立者 token `sub` 自動加入 `caregiver_ids`。client 傳 server-owned 欄位回 400 `INVALID_PARAMETER`。

Response 201 回傳完整物件；`created_at` 與 `updated_at` 初始相同。

### PATCH /elders/{elder_id} — 更新（照護者）

部分更新公開欄位；`lang_preference` 與 `hakka_dialect` 可同次更新，後續新 turn 的 ASR/TTS
共同生效。不得傳 `elder_id`、`caregiver_ids`、`created_at`、`updated_at`。後端只在成功變更時刷新 `updated_at`，`created_at` 保持不變。Response 200 回更新後物件。

`health_notes` 在這裡的語意是**整份取代**。要增刪單筆請走下面兩個端點——`health_notes` 同時被照護者與 Agent 寫入，整份覆寫會把對方期間寫進去的內容一起蓋掉。

### POST /elders/{elder_id}/health_notes — 新增單筆健康註記（照護者）

```json
{ "text": "膝關節退化" }
```

`text` 必填且不得為空白，否則回 400 `INVALID_PARAMETER`；帶 `source` 一律回 400（來源由後端依寫入路徑決定，此端點固定寫入 `caregiver`）。

後端以原子 append 寫入，不讀出再整份寫回，因此與 Agent 的同時寫入不會互相覆蓋。Response 201 回傳更新後的完整長者物件。

### DELETE /elders/{elder_id}/health_notes/{note_id} — 刪除單筆（照護者）

依 `note_id` 移除該筆。Response 200 回傳更新後的完整長者物件；找不到該筆時回 404 `HEALTH_NOTE_NOT_FOUND`。

刪除以「該位置仍是這一筆」為條件寫入，期間若有併發寫入推移了位置會自動重讀重試；持續衝突時回 500 `INTERNAL_ERROR`。

---

## 綁定照護者

`POST /elders` 只綁定建立者自己。第二位家人、以及長者自己開帳號的情況，都需要另一條綁定路徑：家人在長輩手機上輸入自己的照護者 ID，後端比對後綁定。

1. 照護者在自己的 App 看到自己的 ID（`GET /me`）。
2. 家人在長輩手機上輸入該 ID（`POST /elders/{elder_id}/caregivers`）。
3. 長者端列出已綁定的家人（`GET /elders/{elder_id}/caregivers`）。

對外的照護者 ID 不是 Cognito `sub`：`sub` 是 36 字 UUID，抄不動也念不清。後端以 `sub` 穩定衍生一組短 ID 對外，`sub` 本身不出現在任何 response。

### GET /me — 呼叫者自己的身分

照護者要報 ID 給家人，得先看得到自己的 ID。長者帳號呼叫回 403 `FORBIDDEN`。

```json
{ "caregiver_id": "cg_7f3a91c2", "name": "陳志明" }
```

| 欄位 | 說明 |
|---|---|
| `caregiver_id` | `cg_` 後接 8 個小寫十六進位字元，由 Cognito `sub` 穩定衍生。同一個帳號永遠是同一組，不會換 |
| `name` | 顯示名稱，後端保證有值：取 Cognito `name` 屬性，未設定時取信箱 `@` 之前的部分 |

### POST /elders/{elder_id}/caregivers — 綁定照護者（長者本人）

```json
{ "caregiver_id": "cg_7f3a91c2" }
```

只有 token 對應的長者本人可呼叫。`elder_id` 不存在或不屬於 token 中的長者，一律回 404 `ELDER_NOT_FOUND`，不以 403 區分，避免洩漏某個長者是否存在。

`caregiver_id` 比對時大小寫不敏感，前後空白忽略；`cg_` 前綴必填。

#### Response 201（新綁定）／200（早就綁過了）

```json
{
  "caregiver_id": "cg_7f3a91c2",
  "name": "陳志明",
  "linked_at": "2026-07-14T09:06:00+08:00"
}
```

| 欄位 | 說明 |
|---|---|
| `caregiver_id` | 同 `GET /me` |
| `name` | 顯示名稱，規則同 `GET /me`。長輩畫面上要看得出這是誰，但不放完整信箱（PII 最小化，見 `docs/pii.md`） |
| `linked_at` | 首次綁定時間。已綁定的情況回原本的時間，不刷新 |

狀態碼區分結果，App 依此決定要說「連結成功」還是「這位家人已經連結過了」：

- **201**：ID 存在且該照護者尚未綁定這位長者。後端以條件式寫入把 `caregiver_id` 加入 `elders.caregiver_ids`。
- **200**：ID 存在，且該照護者已經綁在這位長者身上。不重複加入、不刷新 `linked_at`。網路重送走同一條，所以綁定是冪等的，不需要 `client_request_id`。
- **404 `CAREGIVER_NOT_FOUND`**：查不到這個 ID（不存在，或不是照護者帳號）。

一位長者可綁定多位照護者，一位照護者也可綁定多位長者，都不設上限以外的限制。

已知限制：ID 可長期使用且沒有對方確認的步驟，所以拿到長輩手機的人可以把任何知道 ID 的照護者綁上去，那位照護者從此看得到這位長輩的資料。綁定必須實際持有長輩手機，是目前唯一的門檻。要收緊的話得加一道照護者側的確認（例如改成由照護者的 App 產生短期一次性碼），屆時 `POST /elders/{id}/caregivers` 的 body 換成該碼，其餘不變。

### GET /elders/{elder_id}/caregivers — 已綁定的家人

長者本人與已綁定的照護者都可讀，其他情況回 404 `ELDER_NOT_FOUND`（同上，不以 403 區分）。結果套用共通分頁規則，按 `linked_at` 由舊到新。

```json
{
  "items": [
    { "caregiver_id": "cg_7f3a91c2", "name": "陳志明", "linked_at": "2026-07-14T09:06:00+08:00", "is_self": false },
    { "caregiver_id": "cg_2b8e04d5", "name": "陳淑芬", "linked_at": "2026-07-20T18:30:00+08:00", "is_self": false }
  ]
}
```

其餘欄位同兌換的 response。沒有綁定時回 `{ "items": [] }`。

`is_self` 為 true 代表這一筆就是呼叫者本人。自我註冊的長輩在 `POST /elders` 建自己的資料時，建立者的 sub 會自動寫進 `caregiver_ids`（那時他還沒有 `elder_id` claim、角色仍是照護者），所以他自己會出現在這份清單裡，而且因為沒有 caregiver lookup 記錄（那是 `GET /me` 才寫的）`name` 是空字串。這一筆不從回應中移除——`caregiver_ids` 是授權用的真實資料，少回一筆會讓畫面與實際綁定對不起來——由 App 依此標示為「自己」。

目前不提供解除綁定：後果嚴重（照護者從此看不到長輩狀況），不該由長輩在自己手機上單獨完成，也不該由任一照護者單方面移除另一位。要做的話需要另外定義誰有權限、以及要不要雙方確認。

---

## 每日摘要

摘要直接公開 `data_status` 與 `pending_session_count`。生成前檢查摘要日期內的相關 sessions；一般事件尚未完成 batch 時不得標示為 complete。

### GET /summaries?elder_id=&from=&to=

`from`/`to` 含首尾，預設最近 7 天。只回已生成日期；無資料回 `{ "items": [] }`。

```json
{
  "items": [
    {
      "elder_id": "eld_a1b2c3d4e5f6",
      "date": "2026-07-14",
      "overview": "截至晚間八點，已處理資料顯示三餐正常並按時服藥；仍有一段對話等待批次整理。",
      "sections": {
        "diet": "三餐正常",
        "activity": "下午到公園散步約 30 分鐘",
        "sleep": "昨晚睡約七小時",
        "medication": "血壓藥已按時服用",
        "wellbeing": "提到膝蓋疼痛，心情平穩",
        "safety": null,
        "other": null
      },
      "routines": {
        "completed": 1,
        "missed": 1,
        "items": [
          { "routine_id": "rtn_001", "title": "吃血壓藥", "status": "done" },
          { "routine_id": "rtn_002", "title": "量血壓", "status": "missed" }
        ]
      },
      "alerts": ["今日多次提到膝蓋疼痛"],
      "interaction_count": 6,
      "data_status": "partial",
      "pending_session_count": 1,
      "generated_at": "2026-07-14T20:00:12+08:00"
    }
  ]
}
```

| 欄位 | 說明 |
|---|---|
| `sections` | 固定七類 `diet/activity/sleep/medication/wellbeing/safety/other`，與 `EventType` 一一對應；七個 key 每次完整回傳，無資料為 null |
| `routines.completed` | `routines.items` 中 `status=done` 的 occurrence 數；pending 不計入 |
| `routines.missed` | `routines.items` 中 `status=missed` 的 occurrence 數；pending 不計入 |
| `routines.items[]` | 固定 `{routine_id,title,status}`；每個 `routine_id + date` 最多一項。`occurrence_cutoff=min(input_through_at, routine_date 的台灣日界結束 23:59:59.999+08:00)`；已有 canonical completion event 時，title 優先取 event 所記 `routine_version` 的不可變定義且 status 為 done，未完成時才取 `occurrence_cutoff` 前最新有效版本 |
| `routines.items[].status` | `pending` \| `done` \| `missed`；生成當下快照 |
| `data_status` | `complete`：`pending_session_count=0` 且相關 closed sessions 的 batch 都完成；否則 `partial` |
| `pending_session_count` | 日期範圍內有 turn 且仍 active/closing，或 closed 但 batch 為 pending/processing/failed 的 session 數 |
| `generated_at` | 摘要生成完成時間 |

排程摘要會等待相關 closed session batch 完成至設定窗口；超過窗口仍有 pending session 時寫 `partial`。batch 後續完成時排程重生成並覆寫為 `complete`。較舊的後端內部 `input_through_at` 不得覆寫較新 cutoff；同一 cutoff 先以完整度決定，`complete` 優先於 `partial`，完整度相同才由較新的 `generated_at` 勝出。`alerts` 無警訊為 `[]`，可參考近期事件標記跨日趨勢。

### POST /summaries/generate — 手動生成

```json
{ "elder_id": "eld_a1b2c3d4e5f6", "date": "2026-07-14" }
```

`date` 預設今天。同步生成，Response 200 回單一摘要物件（結構同列表 item）。手動生成不等待排程窗口，因此可回 `data_status=partial`。只有 `pending_session_count=0` 且所有相關 closed session batch 都 completed 才可回 complete。無對話且確認沒有相關待處理 session 時，七類為 null、alerts 為 `[]`、interaction_count 為 0、pending_session_count 為 0，並可回 complete。

---

## 生活事件

### GET /events?elder_id=&from=&to=&type=

`from`/`to` 預設今天，日期邊界採台灣時間；`type` 選填。結果按 `ts` 最新優先，跨頁順序穩定，`next_token` 必須原樣沿用。

```json
{
  "items": [
    {
      "event_id": "evt_01J8...",
      "elder_id": "eld_a1b2c3d4e5f6",
      "ts": "2026-07-14T09:05:00+08:00",
      "type": "medication",
      "detail": "已服用血壓藥",
      "source": "conversation",
      "conversation_id": "cnv_01J8...",
      "routine_id": "rtn_001"
    },
    {
      "event_id": "evt_01J9...",
      "elder_id": "eld_a1b2c3d4e5f6",
      "ts": "2026-07-14T14:30:00+08:00",
      "type": "wellbeing",
      "detail": "提到膝蓋疼痛，語氣低落",
      "source": "conversation",
      "conversation_id": "cnv_01J9..."
    }
  ],
  "next_token": "eyJ..."
}
```

| 欄位 | 說明 |
|---|---|
| `type` | `diet` \| `activity` \| `sleep` \| `medication` \| `wellbeing` \| `safety` \| `other` |
| `detail` | canonical event 的目前顯示描述；batch 可 enrich 同一 safety event |
| `source` | `conversation` \| `manual` |
| `conversation_id` | 對話事件的主要來源 turn；manual 事件省略 |
| `routine_id` | 對應 routine occurrence 才有 |

資料可見時間：routine completion 與潛在高風險 safety event 可在 `/chat` response 前寫入並立即查得；一般生活事件只在 session close 且 batch materialization 後出現。active 或 batch pending 的缺口不另以公開欄位列出。API 不暴露 extraction track、canonical key、revision、chunk、evidence 列表或其他 extraction internals。

分類與摘要 `sections` 固定七類一一對應：`activity` 指涉及身體動作的日常活動；`wellbeing` 涵蓋身體症狀、生理量測與情緒；`safety` 涵蓋跌倒、走失、詐騙、居家危害等安全事件，與 `alerts` 語意一致；無法歸入前六類的一律為 `other`，回診、約會等行程類事件與家屬互動、看電視等非身體活動也歸 `other`，與 routine occurrence 以 `routine_id` 連結。後端另有更細的分類節點供摘要、統計與 alerts 使用，但不在此 API 暴露。分類不會截斷內容：`detail` 保留事件完整描述，摘要生成讀取 `detail` 全文而不是只看 `type`。同一 safety episode 若先 tool calling（`notify_caregiver`）、後 batch enrich，仍使用同一 `event_id`，`detail` 可以更新得更完整；需要逐字追溯時由後端依 `conversation_id` 讀 conversations，events response 不複製逐字稿。

---

## 例行公事

### GET /routines?elder_id= — 定義列表

回所有生效中定義，供 App 排本地通知。結果套用共通分頁規則，`limit` 上限 100。

```json
{
  "items": [
    {
      "routine_id": "rtn_001",
      "elder_id": "eld_a1b2c3d4e5f6",
      "title": "吃血壓藥",
      "type": "medication",
      "schedule": { "freq": "daily", "time": "09:00" },
      "remind": true,
      "created_by": "caregiver",
      "active": true,
      "created_at": "2026-07-01T10:05:00+08:00"
    },
    {
      "routine_id": "rtn_003",
      "elder_id": "eld_a1b2c3d4e5f6",
      "title": "小明帶去看醫生",
      "type": "other",
      "schedule": { "freq": "once", "date": "2026-07-15", "time": "15:00" },
      "remind": true,
      "created_by": "conversation",
      "active": true,
      "created_at": "2026-07-14T09:05:02+08:00"
    }
  ]
}
```

`schedule`：

| `freq` | 欄位 |
|---|---|
| `daily` | `time` |
| `weekly` | `weekday`（1–7，週一為 1）、`time` |
| `once` | `date`、`time` |

`time` 為 24 小時制 `HH:MM`；`schedule` 只接受該 `freq` 對應的欄位，缺少必要欄位或帶入不適用欄位一律回 400 `INVALID_PARAMETER`。

每週多天建立多筆 weekly routine。routine `type` 與 events 共用七類；口語或手動完成時，completion event 必須沿用該 routine 的 `type`。current GSI 最終一致；`POST/PATCH` response 是強一致最新結果。`/chat` 回 `routines_updated=true` 時，App 應背景呼叫本 endpoint 取得最新定義／狀態。

### GET /routines?elder_id=&date=YYYY-MM-DD — 當日行程

```json
{
  "date": "2026-07-14",
  "items": [
    {
      "routine_id": "rtn_001",
      "title": "吃血壓藥",
      "type": "medication",
      "scheduled_at": "2026-07-14T09:00:00+08:00",
      "status": "done",
      "created_by": "caregiver",
      "completed_at": "2026-07-14T09:05:00+08:00",
      "completed_by": "conversation"
    },
    {
      "routine_id": "rtn_002",
      "title": "量血壓",
      "type": "other",
      "scheduled_at": "2026-07-14T19:00:00+08:00",
      "status": "pending",
      "created_by": "conversation"
    }
  ]
}
```

`status` 為 `pending|done|missed`。所有當日查詢與摘要的歷史解析都使用 `occurrence_cutoff=min(query_or_summary_cutoff, routine_date 的台灣日界結束 23:59:59.999+08:00)`；當日查詢的 `query_or_summary_cutoff` 是本次查詢時間，摘要則是後端 `input_through_at`。若 canonical completion event 已存在，occurrence 固定為 done，`title`、`type`、`scheduled_at` 等顯示定義優先採 event 記錄之 `routine_version` 對應的不可變版本，`completed_at`、`completed_by` 等完成資料採該 event；即使同日稍後改版，也保留完成當時資料。只有未完成 occurrence 才以 `occurrence_cutoff` 前最新有效版本收斂，同日 cutoff 前的新 schedule 可 supersede 舊 schedule但不新增第二筆。歷史日期越過台灣日界後 cutoff 封頂，後續版本不得 retroactively 改寫。未完成且超過唯一 occurrence 的 `scheduled_at + grace period` 才為 missed；grace 預設 120 分鐘，由 `ROUTINE_GRACE_MINUTES` 設定，routines、摘要與統計共用。completion canonical identity 為 `elder_id + routine_id + routine_date`，`routine_version` 只記錄完成採用版本；`completed_by` 為 `conversation|elder|caregiver`。

`created_by` 與定義列表同一組值（`caregiver|conversation`），取該 occurrence 實際採用之版本的建立來源——completion-first 時即 event 所記 `routine_version` 那一版。App 據此標示來源，並判斷長者端可否刪除（見 DELETE）；不需為此另外呼叫定義列表，routine 已刪除但當日仍有 completion 的 occurrence 在定義列表中查無此筆。

### POST /routines — 建立（照護者）

```json
{
  "client_request_id": "5895c75e-05e5-43a0-9092-6feb261d7513",
  "elder_id": "eld_a1b2c3d4e5f6",
  "title": "吃血壓藥",
  "type": "medication",
  "schedule": { "freq": "daily", "time": "09:00" },
  "remind": true
}
```

`client_request_id` 與 `title` 必填，`title` 不得為空字串。後端以 `routine_id="rtn_" + stable-hash(elder_id + authenticated actor sub + client_request_id)` 建立 `version=1`，並以相同 scope 形成 `change_request_id`、保存正規化 `request_hash`，使用 conditional Put／transaction 保護建立。Response 201 回完整物件；並行或重送相同 scoped ID／相同 hash 同樣回 201 與既有物件，不同 payload/hash 回 409 `IDEMPOTENCY_CONFLICT`。長者帳號呼叫回 403 `FORBIDDEN`。對話建立的 routine 由對話大腦的 tool calling 直接寫入，不呼叫此 API。

### PATCH /routines/{routine_id} — 修改（照護者）

必須含新的 `client_request_id`；除該欄位外，只可部分更新 `title`、`type`、`schedule`、`remind`，且至少提供其中一項。server-owned 或未知欄位、以及未提供任何可更新欄位，一律回 400 `INVALID_PARAMETER`。

Response 200 回更新後物件。`change_request_id` scope 固定為 `routine_id + authenticated actor sub + client_request_id`，並保存正規化 request hash；後端以單一 transaction 驗證 scoped request、保護 current version、關閉舊版並建立唯一下一版。並行相同 scope/hash 回同一結果且不建立額外版本；同一 scope 搭配不同 hash 回 409 `IDEMPOTENCY_CONFLICT`。並行的另一次修改先行改版、本次未成立時回 409 `REQUEST_IN_PROGRESS`，client 以同一 `client_request_id` 重試。長者帳號呼叫回 403 `FORBIDDEN`；`routine_id` 不存在回 404 `ROUTINE_NOT_FOUND`。

### DELETE /routines/{routine_id} — 刪除（兩端，長者限自建）

硬刪除指定例行公事的所有版本，並寫入輕量 tombstone（version=0，TTL 7 天後自動清除）供冪等重播。

照護者可刪任一筆。長者只能刪 `created_by=conversation`（自己在對話中建立）的那些；刪照護者建立的回 403 `FORBIDDEN`。與對話工具 `delete_routine` 同一條政策，差別只在入口。

必須帶 Query 參數 `client_request_id`。相同 `client_request_id` 重試冪等回 200；不同 `client_request_id` 對已刪除的 routine 回 409 `IDEMPOTENCY_CONFLICT`。未提供 `client_request_id` 回 400 `MISSING_REQUEST_ID`。

Response 200：`{"deleted": true, "routine_id": "rtn_xxx"}`。`routine_id` 不存在（且無 tombstone）回 404 `ROUTINE_NOT_FOUND`。冪等重播只比對 tombstone 的 `client_request_id`，不再檢查角色——能重播的就是當初刪掉它的那個呼叫端。

### POST /routines/{routine_id}/complete — 手動完成（兩端）

```json
{ "date": "2026-07-14" }
```

`date` 預設今天，格式為 `YYYY-MM-DD`。後端寫 `source=manual` event，event `type` 必須沿用完成當時的 routine type；routine 表不保存完成狀態。Response 200 回該日單一 occurrence 物件，欄位與當日行程的 item 相同。

- 指定日期無排程，或該日有效版本已刪除：400 `ROUTINE_NOT_SCHEDULED`。

- 已完成：冪等回 200，不重複事件。若先前已由 conversation 完成，也命中同一 canonical occurrence event，`completed_at`、`completed_by` 維持首次完成的結果。
- `routine_id` 不存在：404 `ROUTINE_NOT_FOUND`。

---

## 統計

### GET /stats?elder_id=&days=7

```json
{
  "elder_id": "eld_a1b2c3d4e5f6",
  "today": {
    "interaction_count": 6,
    "last_interaction_at": "2026-07-14T15:22:00+08:00"
  },
  "period": {
    "days": 7,
    "interaction_count": 35,
    "active_days": 7
  },
  "routines": {
    "by_routine": [
      { "routine_id": "rtn_001", "title": "吃血壓藥", "completed": 7, "total": 7 }
    ]
  },
  "daily": [
    { "date": "2026-07-14", "interaction_count": 6, "routines_completed": 1, "routines_total": 2 }
  ]
}
```

`elder_id` 必填；`days` 預設 7，可帶 1–31，超出範圍或非整數回 400 `INVALID_PARAMETER`。統計區間為台灣日界下含今天的最近 `days` 天，全部即時彙總，不讀每日摘要。

`interaction_count` 一律是 `/chat` turn 數，不是 session 數，且只計已完成的 turn。`today` 即時計算，當日尚無互動時 `interaction_count` 為 0 並省略 `last_interaction_at`；`period.active_days` 是區間內有互動的天數。`daily` 是前端近 N 日趨勢的逐日資料，依日期遞增，區間內每天都有一筆，無資料的日期以 0 呈現。`routines.by_routine` 只列期間內至少有一次排程的 routine，依 `routine_id` 排序：`total` 是該 routine 在區間內的 occurrence 數（含 pending 與 missed），`completed` 是其中已完成數，`title` 取區間內最新一次排程採用的版本。完成依 canonical events 計數，occurrence 的收斂與 `missed` 判定與當日行程同一套規則。

---

## 端點總覽

| 方法與路徑 | 用途 | 使用者 |
|---|---|---|
| `POST /chat` | realtime 對話（text/audio） | 長者 |
| `POST /chat/sessions/{session_id}/close` | 明確關閉 session（雙管道：API 即時 + EventBridge 收斂） | 長者本人 |
| `GET /elders` | 長者列表 | 兩端 |
| `GET /elders/{id}` | 長者單筆 | 兩端 |
| `POST /elders` | 建立長者 | 照護者 |
| `PATCH /elders/{id}` | 更新長者 | 照護者 |
| `POST /elders/{id}/health_notes` | 新增單筆健康註記 | 照護者 |
| `DELETE /elders/{id}/health_notes/{note_id}` | 刪除單筆健康註記 | 照護者 |
| `GET /me` | 呼叫者自己的照護者 ID 與名稱 | 照護者 |
| `POST /elders/{id}/caregivers` | 輸入照護者 ID 綁定 | 長者本人 |
| `GET /elders/{id}/caregivers` | 已綁定的家人 | 兩端 |
| `GET /summaries` | 含 `data_status` 的摘要 | 照護者 |
| `POST /summaries/generate` | 手動生成摘要，可回 partial | 照護者 |
| `GET /events` | 事件時間軸 | 照護者 |
| `GET /routines` | 定義／當日行程 | 兩端 |
| `POST /routines` | 建立 routine | 照護者 |
| `PATCH /routines/{id}` | 修改／停用 routine | 照護者 |
| `DELETE /routines/{id}` | 刪除 routine | 兩端；長者限 `created_by=conversation` |
| `POST /routines/{id}/complete` | 手動確認完成 | 兩端 |
| `GET /stats` | 互動與行程統計 | 照護者 |
