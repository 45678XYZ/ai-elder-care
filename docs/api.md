# API 規格

所有端點掛在 API Gateway 之下，路徑前綴 `/v1`。

## 共通慣例

- **認證**：所有請求帶 `Authorization: Bearer <Cognito ID Token>`。長者帳號的 token 帶有 `elder_id` claim，只能存取自己的資料；照護者帳號可存取其綁定的所有長者
- **呼叫者身分一律取自 token，不以參數傳遞**（不存在 `caregiver_id` 參數）。照護者與長者的綁定關係存於 `elders` 的 `caregiver_ids` 欄位——`POST /elders` 時建立者自動綁定；各端點依 token 身分過濾與授權，越權回 403
- **格式**：request/response 均為 `application/json; charset=utf-8`
- **時間**：ISO 8601 含時區，如 `2026-07-14T09:05:00+08:00`；日期為 `YYYY-MM-DD`，「一日」的邊界以台灣時間（+08:00）為準
- **ID 前綴**：`eld_`（長者）、`rtn_`（例行公事）、`evt_`（事件）、`cnv_`（對話）
- **分頁**：列表端點支援 `?limit=`（預設 50）與 `?next_token=`。資料超過一頁時，回應最外層帶 `next_token`；將其原樣帶入下一次請求即取得下一頁；回應中沒有 `next_token` 表示已無更多資料。它是不透明的游標字串，前端不需也不應解析其內容
- **錯誤格式**（非 2xx 一律此結構；唯一例外是 401 由 API Gateway 直接回應，body 為其固定格式）：

```json
{ "error": { "code": "ELDER_NOT_FOUND", "message": "找不到指定的長者" } }
```

錯誤分兩層：**HTTP 狀態碼**做粗分類（給 HTTP client、監控等通用工具判讀）；body 內的 **`code`** 做細分類（給前端做 UX 分支的穩定識別碼，一個狀態碼下可有多個 code），`message` 為人讀說明、可能調整，程式勿依賴。

| HTTP 狀態碼 | 使用時機（括號內為此狀態下的 `code`） |
|---|---|
| 400 | 參數缺漏或格式錯誤（`INVALID_PARAMETER`） |
| 401 | token 缺漏或無效（API Gateway 直接擋下） |
| 403 | 存取了未綁定的長者資料（`FORBIDDEN`） |
| 404 | 資源不存在（`ELDER_NOT_FOUND`、`ROUTINE_NOT_FOUND`） |
| 500 | 未預期錯誤（`INTERNAL_ERROR`） |

上表為**全端點通用**的錯誤，各端點不再重複列出；端點特有的錯誤在該端點的說明中註明（如 `POST /chat` 的 400 `AUDIO_TOO_LONG`）。任一端點可能回傳的錯誤＝通用表＋該端點註明的特例。

---

## POST /chat — 對話核心

長者說一句話，取得 AI 語音回覆。`text` 與 `audio` **擇一必填**。

### Request

```json
{
  "elder_id": "eld_001",
  "lang": "zh-TW",
  "text": "我吃過血壓藥了，明天下午三點小明要帶我去看醫生"
}
```

```json
{
  "elder_id": "eld_001",
  "lang": "hak",
  "audio": { "data": "<base64>", "format": "m4a" }
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `elder_id` | string | 必填 |
| `lang` | string | 必填，`zh-TW` \| `hak` |
| `text` | string | 與 `audio` 擇一。裝置端辨識結果 |
| `audio.data` | string | 與 `text` 擇一。base64 音檔，單句 ≤ 60 秒（超過回 400 `AUDIO_TOO_LONG`） |
| `audio.format` | string | `m4a` \| `wav` |

### Response 200

```json
{
  "conversation_id": "cnv_01J8...",
  "transcript": "我吃過血壓藥了，明天下午三點小明要帶我去看醫生",
  "reply_text": "有按時吃藥真棒！明天下午三點小明帶你去看醫生，我幫你記下來了，明天會提醒你。",
  "reply_audio_url": "https://<s3-presigned-url>",
  "routines_updated": true
}
```

| 欄位 | 說明 |
|---|---|
| `conversation_id` | 本輪對話紀錄的 ID；由此輪擷取的事件，其 `conversation_id` 即指向它 |
| `transcript` | `audio` 輸入時回傳後端辨識文字；`text` 輸入時原樣回傳（映射至 DB `elder_transcript`） |
| `reply_text` | AI 語意回覆內文（映射至 DB `ai_respond_text`） |
| `reply_audio_url` | Polly 合成音檔的 S3 presigned URL，15 分鐘有效（映射至 DB `ai_audio_url`） |
| `routines_updated` | 本輪對話有建立行程或完成行程時為 `true`——App 應重拉 `GET /routines` 並重排本地通知 |

> 註：對話紀錄表 (`conversations`) 底層包含發起來源 `source` (`"elder_initiated"` \| `"system_routine_inquiry"`)、長者狀態 `user_status` (`"replied"` \| `"no_response"`)、系統狀態 `system_status` (`"success"` \| `"failed"`)、系統提示語 `ai_prompt_text` (及其語音 `ai_prompt_audio_url`)、長者話語 `elder_transcript` (及其錄音 `elder_audio_s3_key`)、AI 反饋 `ai_respond_text` (及其語音 `ai_respond_audio_url`)，以及三階段時間戳記 (`prompt_sent_at`, `elder_received_at`, `ai_responded_at`) 供長者反應時間與後端 Latency 分析。

事件擷取、記憶更新在後端完成，不回傳給 App。

---

## 長者資料

### GET /elders — 列表

照護者取得綁定的長者列表；長者帳號回傳僅含自己的一筆。

```json
{
  "items": [
    {
      "elder_id": "eld_001",
      "name": "陳阿蘭",
      "nickname": "阿蘭嬤",
      "birth_year": 1948,
      "gender": "female",
      "lang_preference": "zh-TW",
      "address_region": "台北市大安區",
      "health_notes": ["高血壓", "膝關節退化"],
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

### GET /elders/{elder_id} — 單筆

同上單一物件。

### POST /elders — 建立（照護者）

Request 為上述物件去掉 `elder_id`、`created_at`；`name`、`lang_preference` 必填，其餘選填。

Response `201` 回傳建立後的完整物件（含 `elder_id`、`created_at`）。

### PATCH /elders/{elder_id} — 更新（照護者）

Request 帶要更新的欄位即可（部分更新）。Response `200` 回傳更新後完整物件。

---

## 每日摘要

### GET /summaries?elder_id=&from=&to=

`from`/`to` 為日期（含），預設回傳最近 7 天。僅回傳**已生成**摘要的日期（當日通常要到晚間生成後才有），範圍內無任何摘要時回 `{ "items": [] }`。

```json
{
  "items": [
    {
      "elder_id": "eld_001",
      "date": "2026-07-14",
      "overview": "今日整體狀況良好，三餐正常、按時服藥，下午散步三十分鐘，多次提到膝蓋不適，建議留意。",
      "sections": {
        "diet": "早餐稀飯配醬瓜，午餐便當有吃完，晚餐吃得較少",
        "activity": "下午到公園散步約 30 分鐘",
        "sleep": "表示昨晚睡得不錯，約七小時",
        "medication": "血壓藥已按時服用",
        "wellbeing": "多次提到膝蓋疼痛；聊到孫子時很開心，整體心情平穩",
        "other": null
      },
      "routines": {
        "completed": 2,
        "missed": 1,
        "items": [
          { "routine_id": "rtn_001", "title": "吃血壓藥", "status": "done" },
          { "routine_id": "rtn_002", "title": "量血壓", "status": "missed" }
        ]
      },
      "alerts": ["今日三次提到膝蓋疼痛"],
      "interaction_count": 6,
      "generated_at": "2026-07-14T20:00:12+08:00"
    }
  ]
}
```

`sections` 為**固定六類**：`diet` 飲食、`activity` 活動、`sleep` 睡眠、`medication` 用藥、`wellbeing` 身心狀況（身體症狀與情緒）、`other` 其他。六個 key 每次完整回傳，照護者每天看到相同版面、可跨日比較；當日無資料的欄位為 `null`，前端顯示「今日對話未提及」。

`alerts` 為生成摘要時 AI 判斷的注意事項；生成時會一併參考近幾日的事件與摘要，因此能標記跨日趨勢（如「連續兩日提到膝蓋疼痛」）。無警訊時為空陣列。

### POST /summaries/generate — 手動觸發生成

```json
{ "elder_id": "eld_001", "date": "2026-07-14" }
```

`date` 選填，預設今天。同步執行，Response `200` 直接回傳生成好的摘要物件（結構同上）。同日重複觸發會覆寫。該日無任何對話時仍回 `200`：`sections` 六類皆 `null`、`alerts` 為空陣列、`interaction_count` 為 0。

---

## 生活事件

### GET /events?elder_id=&from=&to=&type=

`from`/`to` 為日期，預設今天；`type` 選填過濾。

```json
{
  "items": [
    {
      "event_id": "evt_01J8...",
      "elder_id": "eld_001",
      "ts": "2026-07-14T09:05:00+08:00",
      "type": "medication",
      "detail": "已服用血壓藥",
      "source": "conversation",
      "conversation_id": "cnv_01J8...",
      "routine_id": "rtn_001"
    },
    {
      "event_id": "evt_01J9...",
      "elder_id": "eld_001",
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
| `type` | `diet` \| `activity` \| `sleep` \| `medication` \| `wellbeing` \| `other` |
| `source` | `conversation`（對話擷取）\| `manual`（照護者手動確認行程時產生） |
| `routine_id` | 此事件對應到某筆例行公事時才有（即該次完成紀錄） |

**`type` 分類原則**：與摘要 `sections` 的固定六類一一對應（`wellbeing`＝身心狀況，涵蓋身體症狀與情緒），全系統只有這一套分類。無法歸入前五類的一律 `other`（回診、約會等行程類事件也歸此類，與例行公事的對照靠 `routine_id` 連結），資訊不會遺失：完整內容在 `detail`，摘要生成讀取的是 detail 全文而非 type。

---

## 例行公事

### GET /routines?elder_id= — 定義列表

回傳所有生效中的例行公事定義（App 據此排本地通知）。

```json
{
  "items": [
    {
      "routine_id": "rtn_001",
      "elder_id": "eld_001",
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
      "elder_id": "eld_001",
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

`type` 與 events 的 `type` 同一組枚舉——口語回報或手動確認完成時，寫入的 event 直接沿用該 routine 的 `type`。

`schedule` 依 `freq` 而異：

| `freq` | 欄位 |
|---|---|
| `daily` | `time` |
| `weekly` | `weekday`（1–7，週一為 1）、`time` |
| `once` | `date`、`time` |

每週多天的行程（如週一三五量血壓）建立多筆 `weekly` routine，每筆一個 `weekday`。

### GET /routines?elder_id=&date=YYYY-MM-DD — 當日行程視圖

展開該日應發生的行程與完成狀態（兩端的「今日行程」畫面用這個）。

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
      "completed_at": "2026-07-14T09:05:00+08:00",
      "completed_by": "conversation"
    },
    {
      "routine_id": "rtn_002",
      "title": "量血壓",
      "type": "other",
      "scheduled_at": "2026-07-14T19:00:00+08:00",
      "status": "pending"
    }
  ]
}
```

| 欄位 | 說明 |
|---|---|
| `status` | `pending`（未到時間/待完成）\| `done` \| `missed`（`scheduled_at` 已過且未完成——**查詢時動態判定**，非寫死的狀態；摘要中的完成統計為生成當下的快照） |
| `completed_by` | `conversation`（口語回報自動完成）\| `elder`（長者手動）\| `caregiver`（照護者代確認） |

### POST /routines — 建立（照護者）

```json
{
  "elder_id": "eld_001",
  "title": "吃血壓藥",
  "type": "medication",
  "schedule": { "freq": "daily", "time": "09:00" },
  "remind": true
}
```

Response `201` 回傳建立後的完整物件（含 `routine_id`、`created_by`、`active`、`created_at`，結構同定義列表的 item）。

（長者對話中提到的行程由 chat Lambda 直接寫入，不經此端點。）

### PATCH /routines/{routine_id} — 修改/停用（照護者）

部分更新，欄位同建立；停用傳 `{ "active": false }`。Response `200` 回傳更新後物件。

### POST /routines/{routine_id}/complete — 手動確認完成（兩端皆可）

```json
{ "date": "2026-07-14" }
```

`date` 選填，預設今天。後端同時寫入一筆 `source: "manual"` 的 event 並更新該日完成狀態（與口語回報路徑收斂到同一份資料）。Response `200` 回傳該日 occurrence（結構同當日行程視圖的 item）。

- 該 routine 於指定日期**無排程**（如每週三的行程傳了週四的日期）：400 `ROUTINE_NOT_SCHEDULED`
- 該日**已完成**：冪等，直接回 `200` 與現況，不重複寫入 event

---

## 統計

### GET /stats?elder_id=&days=7

```json
{
  "elder_id": "eld_001",
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
      { "routine_id": "rtn_001", "title": "吃血壓藥", "completed": 7, "total": 7 },
      { "routine_id": "rtn_002", "title": "量血壓", "completed": 5, "total": 7 }
    ]
  },
  "daily": [
    { "date": "2026-07-08", "interaction_count": 4, "routines_completed": 2, "routines_total": 2 },
    { "date": "2026-07-09", "interaction_count": 6, "routines_completed": 1, "routines_total": 2 }
  ]
}
```

- `today` 即時計算；`daily` 逐日資料供前端繪製近 N 天趨勢圖。
- `routines.by_routine` 逐項統計期間內完成情況，僅列期間內有排程的項目。
- `interaction_count` 一律指 `/chat` 的對話輪數（每日摘要中的同名欄位亦同義）。

---

## 端點總覽

| 方法與路徑 | 用途 | 使用者 |
|---|---|---|
| `POST /chat` | 對話（中文/客語 × text/audio） | 長者 |
| `GET /elders` | 長者列表 | 兩端 |
| `GET /elders/{id}` | 長者單筆 | 兩端 |
| `POST /elders` | 建立長者 | 照護者 |
| `PATCH /elders/{id}` | 更新長者 | 照護者 |
| `GET /summaries` | 每日摘要列表 | 照護者 |
| `POST /summaries/generate` | 手動生成摘要 | 照護者 |
| `GET /events` | 生活事件（時間軸） | 照護者 |
| `GET /routines` | 例行公事定義/當日行程 | 兩端 |
| `POST /routines` | 建立例行公事 | 照護者 |
| `PATCH /routines/{id}` | 修改/停用例行公事 | 照護者 |
| `POST /routines/{id}/complete` | 手動確認完成 | 兩端 |
| `GET /stats` | 統計（互動、例行公事完成、逐日趨勢） | 照護者 |
