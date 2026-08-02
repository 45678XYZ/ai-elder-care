# API 概覽

所有端點前綴 `/v1`，認證帶 `Authorization: Bearer <Cognito ID Token>`。

---

## 共通規則

| 項目 | 說明 |
|------|------|
| 格式 | `application/json; charset=utf-8` |
| 時間 | ISO 8601 含時區，日界以台灣時間 (+08:00) 為準 |
| ID 前綴 | `eld_`（長者）、`rtn_`（routine）、`evt_`（事件）、`cnv_`（turn）、`ses_`（session）、`cg_`（照護者）、`hn_`（健康註記） |
| 分頁 | `?limit=`（預設 50）+ `?next_token=`（不透明字串，原樣帶回） |
| 權限 | 長者只能存取自己；照護者只能存取已綁定長者 |

### 錯誤格式

```json
{ "error": { "code": "ELDER_NOT_FOUND", "message": "找不到指定的長者" } }
```

| HTTP | code 範例 | 時機 |
|------|-----------|------|
| 400 | `INVALID_PARAMETER`、`AUDIO_TOO_LONG`、`ROUTINE_NOT_SCHEDULED` | 格式錯誤或不合規則 |
| 401 | `UNAUTHORIZED` | token 缺漏或無效 |
| 403 | `FORBIDDEN` | 越權存取 |
| 404 | `ELDER_NOT_FOUND`、`SESSION_NOT_FOUND`、`ROUTINE_NOT_FOUND` 等 | 資源不存在或不屬於該使用者 |
| 409 | `REQUEST_IN_PROGRESS`、`IDEMPOTENCY_CONFLICT` | 請求衝突 |
| 429 | `THROTTLED` | 超過節流上限 |
| 500 | `INTERNAL_ERROR` | 伺服器錯誤 |

---

## 對話

### POST /chat

長者送出一句話，取得 AI 即時回覆。`text` 與 `audio` 擇一必填。

**Request**

```json
{
  "client_request_id": "ad381d1e-2b96-4dc2-83ac-c58ab0b934db",
  "elder_id": "eld_a1b2c3d4e5f6",
  "session_id": "ses_01J8...",
  "lang": "zh-TW",
  "text": "我吃過血壓藥了"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| client_request_id | string | 必填，UUID；重送沿用 |
| elder_id | string | 必填 |
| session_id | string | 選填；首輪省略由後端建立 |
| lang | `zh-TW` \| `hak` | 必填，決定 ASR/TTS 語言 |
| text | string | 與 audio 擇一 |
| audio.data | string | base64，≤ 60 秒 |
| audio.format | `m4a` \| `wav` | — |

**Response 200**

```json
{
  "conversation_id": "cnv_8f25b1...",
  "session_id": "ses_01J8NEW...",
  "transcript": "我吃過血壓藥了",
  "reply_text": "有按時吃藥真棒！",
  "reply_audio_url": "https://<s3-presigned-url>",
  "routines_updated": true
}
```

| 欄位 | 說明 |
|------|------|
| conversation_id | 本 turn ID |
| session_id | 實際使用的 session（可能是新建的） |
| transcript | audio 的 ASR 結果；text 原樣回傳 |
| reply_text | AI 回覆文字 |
| reply_audio_url | 15 分鐘 presigned URL；失敗時為 `null` |
| routines_updated | 本次有 routine 異動時為 `true` |

### POST /chat/sessions/{session_id}/close

關閉 session，觸發背景 batch 處理。Body 為空 `{}`。

**Response 200**

```json
{
  "session_id": "ses_01J8...",
  "status": "closed",
  "closed_at": "2026-07-14T09:20:00+08:00",
  "batch_status": "pending"
}
```

---

## 長者資料

### GET /elders

照護者取得已綁定長者列表；長者帳號只回自己。

**Response**

```json
{
  "items": [{
    "elder_id": "eld_a1b2c3d4e5f6",
    "name": "陳阿蘭",
    "nickname": "阿蘭嬤",
    "birth_year": 1948,
    "gender": "female",
    "lang_preference": "zh-TW",
    "hakka_dialect": "htia_sixian",
    "address_region": "台北市大安區",
    "health_notes": [
      { "note_id": "hn_9c1f2a4b7d3e", "text": "高血壓", "source": "caregiver", "created_at": "2026-07-01T10:00:00+08:00" }
    ],
    "family": [
      { "relation": "兒子", "name": "陳志明", "note": "每週三來訪" }
    ],
    "habit_note": "早睡早起，喜歡去公園散步",
    "created_at": "2026-07-01T10:00:00+08:00",
    "updated_at": "2026-07-01T10:00:00+08:00"
  }]
}
```

| 欄位 | 說明 |
|------|------|
| gender | `male` \| `female` \| `other` |
| lang_preference | `zh-TW` \| `hak` |
| hakka_dialect | `htia_sixian` \| `htia_hailu` \| `htia_dapu` \| `htia_raoping` \| `htia_zhaoan` \| `htia_nansixian` |
| health_notes[].source | `caregiver`（手動）\| `agent`（AI 補充） |

### POST /elders

照護者建立長者。`name` 必填，建立者自動綁定。Response 201。

### PATCH /elders/{elder_id}

部分更新，不可傳 server-owned 欄位。Response 200。

### POST /elders/{elder_id}/health_notes

```json
{ "text": "膝關節退化" }
```

原子 append，不與 Agent 寫入衝突。Response 201 回完整長者物件。

### DELETE /elders/{elder_id}/health_notes/{note_id}

依 note_id 移除。Response 200 回完整長者物件；找不到回 404 `HEALTH_NOTE_NOT_FOUND`。

---

## 綁定照護者

### GET /me

照護者取得自己的短 ID 與名稱。

```json
{ "caregiver_id": "cg_7f3a91c2", "name": "陳志明" }
```

### POST /elders/{elder_id}/caregivers

長者本人輸入照護者 ID 完成綁定。

```json
{ "caregiver_id": "cg_7f3a91c2" }
```

**Response 201（新綁定）/ 200（已綁定）**

```json
{ "caregiver_id": "cg_7f3a91c2", "name": "陳志明", "linked_at": "2026-07-14T09:06:00+08:00" }
```

### GET /elders/{elder_id}/caregivers

列出已綁定照護者，按 `linked_at` 由舊到新。

```json
{
  "items": [
    { "caregiver_id": "cg_7f3a91c2", "name": "陳志明", "linked_at": "...", "is_self": false }
  ]
}
```

---

## 每日摘要

### GET /summaries?elder_id=&from=&to=

`from`/`to` 含首尾，預設最近 7 天。

**Response**

```json
{
  "items": [{
    "elder_id": "eld_a1b2c3d4e5f6",
    "date": "2026-07-14",
    "overview": "三餐正常並按時服藥；仍有一段對話等待整理。",
    "sections": {
      "diet": "三餐正常",
      "activity": "下午散步 30 分鐘",
      "sleep": "昨晚睡七小時",
      "medication": "血壓藥已按時服用",
      "wellbeing": "提到膝蓋痛",
      "safety": null,
      "other": null
    },
    "routines": {
      "completed": 1,
      "missed": 1,
      "items": [
        { "routine_id": "rtn_001", "title": "吃血壓藥", "status": "done" }
      ]
    },
    "alerts": ["今日多次提到膝蓋疼痛"],
    "interaction_count": 6,
    "data_status": "partial",
    "pending_session_count": 1,
    "generated_at": "2026-07-14T20:00:12+08:00"
  }]
}
```

| 欄位 | 說明 |
|------|------|
| sections | 固定七類，無資料為 `null` |
| data_status | `complete`（全部 batch 完成）\| `partial`（仍有待處理 session） |
| pending_session_count | 尚未完成 batch 的 session 數 |
| alerts | 無警訊為 `[]` |

### POST /summaries/generate

```json
{ "elder_id": "eld_a1b2c3d4e5f6", "date": "2026-07-14" }
```

同步生成，Response 200 回單一摘要物件。

---

## 生活事件

### GET /events?elder_id=&from=&to=&type=

`from`/`to` 預設今天；`type` 選填。按 `ts` 最新優先。

**Response**

```json
{
  "items": [{
    "event_id": "evt_01J8...",
    "elder_id": "eld_a1b2c3d4e5f6",
    "ts": "2026-07-14T09:05:00+08:00",
    "type": "medication",
    "detail": "已服用血壓藥",
    "source": "conversation",
    "conversation_id": "cnv_01J8...",
    "routine_id": "rtn_001"
  }]
}
```

| 欄位 | 說明 |
|------|------|
| type | `diet` \| `activity` \| `sleep` \| `medication` \| `wellbeing` \| `safety` \| `other` |
| source | `conversation` \| `manual` |
| routine_id | 選填，對應 routine 完成時才有 |

---

## 例行公事

### GET /routines?elder_id=

回所有生效定義。

```json
{
  "items": [{
    "routine_id": "rtn_001",
    "elder_id": "eld_a1b2c3d4e5f6",
    "title": "吃血壓藥",
    "type": "medication",
    "schedule": { "freq": "daily", "time": "09:00" },
    "remind": true,
    "created_by": "caregiver",
    "active": true,
    "created_at": "2026-07-01T10:05:00+08:00"
  }]
}
```

**schedule 格式**

| freq | 欄位 |
|------|------|
| `daily` | `time` |
| `weekly` | `weekday`（1-7，週一=1）、`time` |
| `once` | `date`、`time` |

### GET /routines?elder_id=&date=YYYY-MM-DD

當日行程含完成狀態。

```json
{
  "date": "2026-07-14",
  "items": [{
    "routine_id": "rtn_001",
    "title": "吃血壓藥",
    "type": "medication",
    "scheduled_at": "2026-07-14T09:00:00+08:00",
    "status": "done",
    "created_by": "caregiver",
    "completed_at": "2026-07-14T09:05:00+08:00",
    "completed_by": "conversation"
  }]
}
```

| 欄位 | 說明 |
|------|------|
| status | `pending` \| `done` \| `missed` |
| completed_by | `conversation` \| `elder` \| `caregiver` |

### POST /routines

照護者建立。`client_request_id` 與 `title` 必填。

```json
{
  "client_request_id": "5895c75e-...",
  "elder_id": "eld_a1b2c3d4e5f6",
  "title": "吃血壓藥",
  "type": "medication",
  "schedule": { "freq": "daily", "time": "09:00" },
  "remind": true
}
```

Response 201 回完整物件。

### PATCH /routines/{routine_id}

必須含 `client_request_id`，可更新 `title`、`type`、`schedule`、`remind`。Response 200。

### DELETE /routines/{routine_id}?client_request_id=

照護者可刪任一筆；長者只能刪 `created_by=conversation`。

Response 200：`{ "deleted": true, "routine_id": "rtn_xxx" }`

### POST /routines/{routine_id}/complete

```json
{ "date": "2026-07-14" }
```

`date` 預設今天。Response 200 回該日 occurrence 物件。已完成則冪等回 200。

---

## 統計

### GET /stats?elder_id=&days=7

`days` 預設 7，可帶 1–31。

**Response**

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

---

## 端點總覽

| 方法 | 路徑 | 用途 | 使用者 |
|------|------|------|--------|
| POST | /chat | AI 對話 | 長者 |
| POST | /chat/sessions/{id}/close | 關閉 session | 長者 |
| GET | /elders | 長者列表 | 兩端 |
| GET | /elders/{id} | 長者單筆 | 兩端 |
| POST | /elders | 建立長者 | 照護者 |
| PATCH | /elders/{id} | 更新長者 | 照護者 |
| POST | /elders/{id}/health_notes | 新增健康註記 | 照護者 |
| DELETE | /elders/{id}/health_notes/{note_id} | 刪除健康註記 | 照護者 |
| GET | /me | 自己的照護者 ID | 照護者 |
| POST | /elders/{id}/caregivers | 綁定照護者 | 長者 |
| GET | /elders/{id}/caregivers | 已綁定家人 | 兩端 |
| GET | /summaries | 每日摘要 | 照護者 |
| POST | /summaries/generate | 手動生成摘要 | 照護者 |
| GET | /events | 事件時間軸 | 照護者 |
| GET | /routines | 定義/當日行程 | 兩端 |
| POST | /routines | 建立 routine | 照護者 |
| PATCH | /routines/{id} | 修改 routine | 照護者 |
| DELETE | /routines/{id} | 刪除 routine | 兩端 |
| POST | /routines/{id}/complete | 手動完成 | 兩端 |
| GET | /stats | 統計 | 照護者 |
