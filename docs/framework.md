# 客照e點通 App — 系統開發框架

## Context

本文件定義客照e點通系統的架構、資料模型與寫入規則。系統分三大模組：**A 語音互動陪伴、B 生活記錄與智慧摘要、C 照護者資訊介面**。

已確認的決策：

- **App：Flutter**（單一 App，登入後依角色切換長者模式／照護者模式）
- **語言策略：中文先行、客語第二階段**
- **Flutter 端做薄，智慧邏輯放 AWS 後端（Python Lambda）**
- **IaC：Terraform**
- **對話處理採 Hybrid realtime/batch**：`POST /chat` 透過 AgentCore Runtime 的 tool calling 同步處理 routine 建立、修改、停用、完成，以及潛在高風險 safety events；session 關閉後才由 batch 補齊一般生活事件
- **長期記憶**：使用 AWS AgentCore 服務管理，不自建 DynamoDB memories 表

## 系統架構

```mermaid
flowchart TB
    subgraph app["Flutter App（長者／照護者模式）"]
        elder["長者模式<br/>免手持語音迴圈<br/>錄音上傳 + 音訊播放"]
        caregiver["照護者模式<br/>行程、事件、摘要、統計"]
    end

    subgraph aws["AWS"]
        cognito["Cognito"]
        apigw["API Gateway REST + JWT"]
        chat["chat realtime Lambda<br/>POST /chat"]
        tools["tools Lambda<br/>對話大腦的工具箱<br/>complete_routine / create_routine / notify_caregiver"]
        closer["session closer Lambda<br/>client close + periodic idle close"]
        periodic["EventBridge periodic session closer"]
        queue["SQS batch queue"]
        dlq["SQS DLQ"]
        dlqreconciler["DLQ reconciler Lambda<br/>DLQ event source"]
        alerting["CloudWatch alarm / SNS"]
        batch["batch extractor Lambda<br/>static topic chunks<br/>normal events + in-memory dedup"]
        summaryschedule["EventBridge summary schedule"]
        summary["daily summary generator Lambda"]
        apis["資料 API Lambda<br/>elders / summaries / events<br/>routines / stats"]
        asr["後端 ASR 模組<br/>Canonical Audio + 路由 + 備援鏈<br/>backend/src/shared/asr"]
        asrproviders["AWS ASR providers<br/>Transcribe zh-TW Streaming<br/>CE 備援 + Formo 六腔固定 prompt"]
        brain["AgentCore Runtime<br/>LangGraph 對話大腦 + 託管長期記憶"]
        model["Bedrock foundation model<br/>chat structured output + batch extraction"]
        rules["deterministic safety rules"]
        tts["後端 TTS 模組<br/>語言/六腔路由 + 同語言備援"]
        ttsmodels["TTS providers<br/>OmniVoice / VoxHakka / BreezyVoice / Polly"]
        ddb[("DynamoDB<br/>conversations / events / summaries / routines")]
        s3[("S3<br/>TTS 音檔、衛教文件")]
        kb["Bedrock Knowledge Bases"]
    end

    app <-->|HTTPS| apigw
    app -.->|登入| cognito
    apigw --> chat
    apigw -->|POST /chat/sessions/{session_id}/close| closer
    apigw --> apis
    periodic -->|idle sweep / batch recovery sweep| closer
    chat -->|audio| asr
    asr -->|transcript| chat
    asr -->|中文主力| asrproviders
    asr -.->|CE/Formo 僅在模型通過核准後| asrproviders
    chat -->|invoke agent runtime| brain
    brain -->|converse| model
    brain -->|RAG retrieval| kb
    brain -->|tool calling| tools
    tools -->|routines + safety events| ddb
    chat -->|turn + session| ddb
    chat -->|reply text + lang + profile 腔調| tts
    tts -.->|僅已核准 route| ttsmodels
    ttsmodels --> s3
    closer -->|freeze snapshot, then closed| ddb
    closer -->|enqueue after closed| queue
    queue --> batch
    batch -->|batch extraction| model
    batch -->|normal events + batch state| ddb
    queue -->|重試耗盡| dlq
    dlq -->|DLQ event source| dlqreconciler
    dlqreconciler -->|conditional failed convergence| ddb
    dlqreconciler -->|安全化錯誤告警| alerting
    summaryschedule --> summary
    summary -->|summary generation| model
    summary --> ddb
    apis --> ddb
```

- **語音對話迴圈**：App 錄音 → `POST /chat` 後端 ASR 辨識 → 生成回覆 → 播放 → 自動再聆聽；`/chat` 不等待 session batch。
- `POST /chat` 接受 `{text}` 或 `{audio}`，語言為 `zh-TW` 或 `hak`。text 直接進對話流程；audio 由後端 ASR 轉文字後走相同 realtime 快路徑。
- **後端 ASR** 採 remote-only 架構：Lambda 不執行模型推論。`zh-TW` 以 Amazon Transcribe Streaming 為主力、Taiwan-Tongues CE 為備援；`hak:<六腔>` 以對應 Formo 固定-prompt SageMaker endpoint 為主力、共用 CE 為備援。CE/Formo 必須逐模型通過 staging/runtime、授權、存取、配額與容量核准，未核准時一律 fail closed；Transcribe 全程 memory-only，不使用 batch/S3。ASR 子系統完整架構見 [`docs/asr/framework.md`](asr/framework.md)；程式碼層見 [`backend/src/shared/asr/README.md`](../backend/src/shared/asr/README.md)。
- **後端 TTS** 同樣採 remote-only 與設定驅動 route。`lang` 明確決定中文或客語；客語六腔只讀 elder profile 並保存 turn 快照。客語失敗不得改用中文 voice；所有 TTS provider 失敗時仍提交文字 turn，`reply_audio_url=null`。完整規格見 [`docs/tts/framework.md`](tts/framework.md)。
- AgentCore Runtime 的 tool calling 是對話中 routine 變更與 safety 事件的主要處理路徑：大腦在回應 chat Lambda 之前先呼叫 tools Lambda 寫入 completion event 或發送安全通知，並在回應 payload 明確回報 `routines_updated`。安全通知由 `notify_caregiver` tool 即時發送（寫 DynamoDB + SNS），不需額外旗標回傳。一般生活事件仍由 session close 後的 batch pipeline 萃取，不透過 tool calling。
- batch extractor 的分類前先做候選概念檢索：以 Bedrock embedding 取查詢向量，向 S3 Vectors 的概念索引取 Top-K 候選後才呼叫分類模型；同一個 embedding 供應者也用於 turn 切分。索引維度在建立時固定，因此 index 名稱帶模型與維度，模型抽換以新索引並存、切換環境變數完成。
- App 在使用者離開、停止免手持互動或切換對象時呼叫 close endpoint；未明確關閉的閒置 session 由 EventBridge 週期性收斂。

## API 一覽

| API | 用途 | 使用者 |
|---|---|---|
| `POST /chat` | realtime 對話快路徑（中文/客語 × text/audio） | 長者模式 |
| `POST /chat/sessions/{session_id}/close` | 冪等關閉 session 並觸發離線 materialization | 長者模式 |
| `GET /elders`、`POST /elders`、`PATCH /elders/{id}` | 長者資料 | 兩端／照護者 |
| `GET /me`、`POST /elders/{id}/caregivers`、`GET /elders/{id}/caregivers` | 輸入照護者 ID 綁定家人 | 照護者／長者本人 |
| `GET /summaries`、`POST /summaries/generate` | 含 `data_status` 的每日摘要 | 照護者模式 |
| `GET /events` | 生活事件時間軸 | 照護者模式 |
| `GET /routines`、`POST/PATCH /routines`、`POST /routines/{id}/complete` | 行程與完成確認 | 兩端 |
| `GET /stats` | 互動與行程統計 | 照護者模式 |

登入走 Cognito SDK；TTS 音檔以 S3 presigned URL 回傳。`docs/api.md` 是前後端唯一契約。

## 功能框架

| 功能 | 定位 |
|---|---|
| 語音互動陪伴（Module A） | 免手持語音對話，回應具時間、節日、近期對話、既有事件與 AgentCore 長期記憶等情境；可查詢自身紀錄與行程 |
| 生活記錄（Module B） | realtime 僅同步 routine 變更／完成與潛在高風險 wellbeing/safety signals；session 關閉後由 batch 萃取一般事件 |
| AI 記憶（Module B） | 長期記憶由 AWS AgentCore 服務管理，不自建 DynamoDB memories 表 |
| 每日摘要（Module B） | 固定七類結構，以 `complete`／`partial` 表示相關 session 的 batch 是否完成 |
| 例行公事與提醒（Module B） | 照護者或 realtime 對話建立／修改／停用；完成狀態由 events 衍生；兩端以本地通知提醒 |
| 照護者介面（Module C） | 長者管理、每日摘要、統計圖表、事件時間軸 |
| 衛教知識庫（進階） | 公開衛教內容供 RAG；僅供參考、不做醫療診斷 |
| PII 保護 | Cognito、傳輸／靜態加密、同意與保留政策、模擬 persona |

## 資料模型（DynamoDB）

| Table | 內容 |
|---|---|
| `elders` | 長者 persona、照護者綁定、語言與生活背景 |
| `conversations` | turn、同表 session metadata、凍結的 session snapshot 與 static chunk manifest |
| `events` | 實際發生事件與 routine 完成的 canonical 紀錄 |
| `daily_summaries` | 帶 `data_status` 的衍生快照 |
| `routines` | 不可變版本的例行公事計畫；完成狀態由 events 動態衍生 |

> 長期記憶（memories）由 AWS AgentCore 服務管理，不使用 DynamoDB 自建表。

### `elders` 表

Base table：PK `elder_id` (String)。

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `elder_id` | String | 是 | `eld_<12-lowercase-hex>`；後端產生 |
| `name` | String | 是 | 姓名 |
| `nickname` | String | 否 | 暱稱 |
| `birth_year` | Number | 否 | 出生年份 |
| `gender` | String | 否 | `male` \| `female` \| `other` |
| `lang_preference` | String | 是 | `zh-TW` \| `hak` |
| `hakka_dialect` | String | 是 | ASR/TTS 共用；六腔之一，預設 `htia_sixian` |
| `address_region` | String | 否 | 居住區域 |
| `health_notes` | List[String] | 是 | 預設 `[]` |
| `family` | List[Object] | 是 | 預設 `[]`；元素含 `relation`, `name`, `note` |
| `habit_note` | String | 否 | 習慣與喜好 |
| `caregiver_ids` | List[String] | 是 | Server-owned；至少含建立者 Cognito `sub` |
| `created_at`, `updated_at` | String | 是 | ISO 8601、`+08:00` |

長者範例：

```json
{
  "elder_id": "eld_a1b2c3d4e5f6",
  "name": "陳阿蘭",
  "nickname": "阿蘭嬤",
  "birth_year": 1948,
  "gender": "female",
  "lang_preference": "zh-TW",
  "hakka_dialect": "htia_sixian",
  "address_region": "台北市大安區",
  "health_notes": ["高血壓", "膝關節退化"],
  "family": [
    { "relation": "兒子", "name": "陳志明", "note": "在台北工作，每週三來訪" },
    { "relation": "孫子", "name": "小明", "note": "高中生" }
  ],
  "habit_note": "早睡早起，喜歡去公園散步、看歌仔戲",
  "caregiver_ids": ["<creator-cognito-sub>"],
  "created_at": "2026-07-01T10:00:00+08:00",
  "updated_at": "2026-07-01T10:00:00+08:00"
}
```

- `elder_id`、`caregiver_ids`、`created_at`、`updated_at` 為 server-owned；`elder_id = "eld_" + uuid4().hex[:12]`，App 或模型不得指定。
- `caregiver_ids` 只有兩條寫入路徑：`POST /elders` 加入建立者的 Cognito `sub`，以及長者在 `POST /elders/{id}/caregivers` 輸入照護者 ID 後以條件式寫入加入該照護者（見 `docs/api.md`「綁定照護者」）。`PATCH /elders` 一律不接受這個欄位。對外只回 `cg_` 開頭、由 `sub` 穩定衍生的識別，不暴露 `sub`；後端需要能由 `cg_` 反查帳號，反查方式由後端決定。
- `POST /elders` 未提供 `health_notes` 或 `family`，或其值為空時，後端補為 `[]`；`caregiver_ids` 至少加入建立者 Cognito token 的 `sub`，不得由 request 指定。
- 建立時 `created_at=updated_at`；`PATCH /elders/{elder_id}` 成功變更公開欄位時由後端刷新 `updated_at`，不得改寫 `created_at`。
- `GET /elders`：照護者只回 `caregiver_ids` 包含其 token `sub` 的長者；長者只回 `elder_id == token.elder_id` 的自己一筆。`GET /elders/{elder_id}` 以 Base table `GetItem` 查單筆，長者只能查自己，照護者只能查已綁定長者。
- elders 不設定 TTL；資料依 PII 政策保留／刪除，DynamoDB 啟用 AWS owned key 靜態加密。

### `conversations` 表

#### Key、item 類型與索引

| 名稱 | Key Schema | 用途 |
|---|---|---|
| Base table | PK `elder_id` + SK `record_id` | 同表保存 turn 與 session；支援強一致讀寫 |
| GSI `conversations-by-time` | PK `elder_id` + SK `conversation_time_key` | 僅 turn；按時間查詢 |
| GSI `conversations-by-session` | PK `session_id` + SK `conversation_time_key` | 僅 turn；取得 session 有序對話 |
| sparse GSI `sessions-by-state` | PK `session_state_key` + SK `session_state_time_key` | 找 idle close 候選及 batch recovery 候選 |

`record_id` 只有兩類：

- `TURN#<conversation_id>`：對話輪次。
- `SESSION#<session_id>`：session metadata 與凍結 snapshot。

`conversation_time_key=<created_at>#<conversation_id>`。`sessions-by-state` 的值與用途精確如下：

- `session_state_key=ACTIVE`，`session_state_time_key=<last_activity_at>#<elder_id>#<session_id>`：找 idle active 或尚待 close 收斂的候選。
- `session_state_key=BATCH#PENDING|BATCH#PROCESSING|BATCH#FAILED`，`session_state_time_key=<closed_at>#<elder_id>#<session_id>`：分別找待派送、lease-expired recovery 或人工 replay 候選；`failed` 不供正常 worker／自動 recovery claim。
- batch 完成後移除這兩個 GSI 欄位。

GSI 只用來找候選，不能當成 freeze、snapshot 或 ownership 判斷的真理來源。closer／worker 對每個候選都必須回 Base table 做強一致讀取，再以條件式寫入或 transaction 推進。

#### Turn 欄位

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `elder_id`, `record_id`, `item_type` | String | 是 | `record_id=TURN#...`；`item_type=conversation` |
| `conversation_id` | String | 是 | `cnv_<identifier>`；由 `elder_id + client_request_id` 穩定產生 |
| `conversation_time_key` | String | 是 | GSI 排序鍵 (`<created_at>#<conversation_id>`) |
| `client_request_id` | String | 是 | 客戶端請求唯一 ID (冪等 UUID) |
| `request_hash` | String | 是 | 正規化請求 payload hash |
| `request_status` | String | 是 | `processing` \| `completed` \| `failed` |
| `request_lease_owner`, `request_lease_until` | String | 否 | `/chat` 請求租約鎖 |
| `error_http_status`, `error_code`, `error_message` | String / Number | 否 | 終端失敗時記錄之 HTTP 碼、錯誤代碼與訊息（供冪等重播） |
| `session_id` | String | 是 | `ses_<identifier>` |

| `created_at` | String | 是 | 固定毫秒、`+08:00` |
| `lang` | String | 是 | `zh-TW` \| `hak` |
| `hakka_dialect` | String | 否 | `lang=hak` 時 reserve 保存的 elder profile 腔調快照 |
| `input_type` | String | 是 | `text` \| `audio` |
| `elder_transcript`, `ai_respond_text` | String | 是 | 長者內容、AI 回覆內容 |
| `ai_respond_audio_s3_key` | String | 否 | 只存 S3 object key (不存公開 URL) |
| `elder_received_at`, `ai_responded_at` | String | 否 | 長者發話接收與 AI 回覆完成時間戳記 |
| `routines_updated` | Boolean | 是 | 本 turn 是否觸發 routine 狀態更新 |

tool calling 副作用（routine create/update/deactivate/complete、safety event 寫入）由 AgentCore Runtime 在回應 chat Lambda 之前同步完成。所有離線 Topic Chunk 萃取狀態（Manifest、Topic 分塊與 Chunk 萃取狀態）100% 集中維護於 Session 的 `chunk_manifest` 中，Turn Item 不另外保存 `batch_*` 狀態欄位。所有對話皆為長者主動發話（長者先輸入文字或語音，AI 再合成語音回覆）。音訊欄位只存 S3 object key（`ai_respond_audio_s3_key`），不在 DynamoDB 保存公開 URL；API 每次回傳時動態簽發 15 分鐘 presigned URL。

#### Session metadata 欄位



| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `elder_id`, `record_id`, `item_type` | String | 是 | `record_id=SESSION#...`；`item_type=session` |
| `session_id` | String | 是 | `ses_<identifier>` |
| `state` | String | 是 | 只允許 `active` → `closing` → `closed` |
| `created_at`, `last_activity_at` | String | 是 | 會話建立與最後有效活動時間 |
| `closed_at` | String | 否 | snapshot 完成並進入 `closed` 的時間 |
| `close_reason` | String | 否 | `idle` \| `client_requested` \| `max_turns` |
| `turn_ids` | List[String] | 是 | 按接納順序保存的有界 completed turn ID 列表 |
| `turn_count` | Number | 是 | `turn_ids` 數量 (上限 50/100) |
| `inflight_turn_ids` | List[String] | 是 | 飛在半空中 (`processing`) 的 turn ID 列表 |
| `inflight_turn_count` | Number | 是 | `inflight_turn_ids` 數量 (受控於 `SESSION_MAX_INFLIGHT_TURNS`) |
| `input_bytes` | Number | 是 | 對話總輸入位元組累積值 (受控於 `SESSION_MAX_INPUT_BYTES`) |
| `recent_conversation_ids` | List[String] | 否 | 近期 active 對話 context ID 列表 |
| `session_snapshot_hash` | String | 否 | frozen ordered turns 與內容版本的 stable hash |

| `session_state_key`, `session_state_time_key` | String | 否 | sparse GSI 欄位 |
| `batch_status` | String | 否 | closed 後為 `pending` \| `processing` \| `completed` \| `failed` |
| `batch_attempts` | Number | 是 | 預設 `0` |
| `batch_lease_owner`, `batch_lease_until` | String | 否 | at-least-once consumer lease |
| `batch_error` | Map | 否 | 最近失敗的安全化 code/message/時間 |
| `batch_extractor_pipeline` | String | 否 | 使用的 pipeline 名稱（`direct_seven`） |
| `batch_extractor_version` | String | 否 | 完成 session 的 extractor 版本 |
| `batch_completed_at` | String | 否 | 全部 chunk 完成時間 |
| `schema_version` | Number | 是 | 初始 `1` |


`turn_ids` 最大 100，實際上限由 `SESSION_MAX_TURNS` 設定且不得高於 100；`inflight_turn_ids`／`inflight_turn_count` 另受 `SESSION_MAX_INFLIGHT_TURNS` 約束，接納 transaction 必須同時保證 completed + inflight 不超過 session 上限。`input_bytes` 另有 `SESSION_MAX_INPUT_BYTES`；接納下一 turn 會超限時，先以 `max_turns` 或 `max_input_bytes` 自動 close 原 session，再用新 session 接納，避免 session item 接近 DynamoDB 400 KB。`recent_conversation_ids` 只供 active 對話 context，不替代完整有序 `turn_ids`。

`/chat` routing 順序固定以 turn 冪等判定先於任何 session 選擇、建立或 reserve：

1. 後端先以 `elder_id` scope + `client_request_id` 強一致查詢 existing turn，並比對正規化 `request_hash`。同 scope/ID 但 hash 不同回 idempotency conflict；相同 hash 且為 `completed`／`failed` 時直接 replay 原 terminal 結果，其中 `completed` 即使原 session 已為 `closing`／`closed` 仍回原結果，不建立新 session，也不重新 reserve。`processing` 且 request lease 尚有效時回 409；lease expired 時才可條件式接管原 turn、原 session 與既有 reservation，不重新執行 session routing 或 reserve。
2. 只有查無 existing turn 的全新 `client_request_id` 才做 session 選擇；必要時建立新 `active` session，再以單一 DynamoDB transaction 建立 `request_status=processing` 的 turn lease，並只在 session 仍為 `active` 時將 conversation ID reserve 到 `inflight_turn_ids`、遞增 `inflight_turn_count`。turn 與 inflight 上限必須同時成立。

相同 ID/hash 已 reserve 的既有 processing turn 只能依前述 lease 規則回 409 或接管，不可重複 reserve。AI／rules 產生的每 turn routine/event actions 有固定上限，使最終 `TransactWrite` 永遠不超過 DynamoDB 100 items。

final success 必須用單一 `TransactWrite`，並以該 ID 是下一個可提交 reservation 為條件，原子提交 turn 的 `request_status=completed` 與穩定 response、所有 realtime routine/event mutations、從 inflight 移除該 ID、按接納順序 append `turn_ids`／更新 `recent_conversation_ids`，以及更新 `turn_count`、`input_bytes`、`last_activity_at`；任何一項條件失敗都不得留下部分業務副作用。terminal failure 以 transaction／條件式更新把 turn 標成 `failed`、寫穩定安全化錯誤並移除 inflight reservation，且保證不含任何 routine/event side effects。business commit 一旦成功，turn 必為 `completed`；其後 HTTP／API Gateway delivery 失敗不得回寫 failed，相同 ID replay completed 結果即可，避免 client 改用新 ID 重複副作用。

`closed` 後 `inflight_turn_ids=[]` 且 `inflight_turn_count=0`；不得新增、刪除或重排 turn，也不得修改 state、close metadata、計數、snapshot hash 或 frozen input。batch worker 唯一可變更的是 session 上明列的 batch control/result 欄位；不得 reopen session 或改變 frozen snapshot。

#### Session close、SQS recovery 與 DLQ

1. 對前述查無 existing turn 的全新 `/chat` 請求，只把 turn 接納進仍為 `active` 且未 idle、未達上限的 session。`/chat` acceptance 與 close 以 session condition 競爭：reserve 先成功時 close 會看到 inflight；`active→closing` 先成功時 chat 不得 append 原 session，必須建立新 active session後才 reserve。existing completed/failed turn 一律直接 replay，不套用本項 session routing。
2. close 可由長者呼叫 endpoint，或由 EventBridge periodic session closer 查詢 `sessions-by-state` 的 `ACTIVE` 候選。候選必須回 Base table 強一致讀取後才判斷 idle 或繼續尚未收斂的 close。client close 沒有 `client_request_id`：`active` 且 `inflight_turn_count=0` 時可完成 close 並回 200；仍有 inflight，或 `closing` 尚未收斂時回 409 `REQUEST_IN_PROGRESS`，App 以同一 close call 重試；已 `closed` 則冪等回 200。EventBridge 不因 client 收到 409 而停止收斂。
3. closer 只可在 `inflight_turn_count=0` 時以條件式寫入／transaction 做 `active→closing`，停止接受新 turn並 freeze ordered `turn_ids`、`turn_count`、`input_bytes`。`closing` 重入同一流程，不建立另一份 snapshot。
4. periodic closer 遇 `closing` 或 inflight 長時間未清空時，必須強一致讀取每個 inflight turn：terminal turn 依結果修復 reservation；processing 且 lease 到期者可條件式接管並繼續，或以安全的 terminal failure transaction 寫 `failed` 並移除 reservation。非 terminal turn 或 `inflight_turn_count>0` 時一律不得 freeze／轉成 `closed`。
5. closer 依 frozen `turn_ids` 對 Base table 做 `ConsistentRead=true` 的 BatchGet；超過單次 BatchGet 上限時分批讀取。結果必須全數存在、`session_id` 相同、ID 無重複、request 都為 `completed`，且數量／輸入 bytes 與 session 相符；否則 close 不得完成並須告警。
6. 驗證後依固定 canonical serialization 計算 `session_snapshot_hash`，再以條件式寫入做 `closing→closed`，設定 `closed_at`、`batch_status=pending` 與 `BATCH#PENDING` GSI 欄位。`closed` 在 batch 開始前已成立，不以 batch 成功作為 closed 條件。
7. closer 在 closed 持久化後送 SQS。DynamoDB transaction 與 SQS SendMessage 非原子；若在兩者間中斷，EventBridge 對 `BATCH#PENDING` 的 recovery sweep 會依 Base table 現況重投。重投可能產生 duplicate，consumer 必須按 `session_id + session_snapshot_hash`、lease、canonical keys 與條件式寫入達成冪等，不能假設 exactly-once。
8. 正常 worker 只可條件式把 `pending→processing`，或在 `processing` 且 lease expired 時由 delivery／recovery 接管；不可從 `failed` claim。成功時條件式設 `completed`、清 lease並移除 batch GSI 欄位。queued duplicate 必須以訊息的 `session_id + session_snapshot_hash` 強一致核對 Base table：已為 `failed` 或 `completed` 時直接 ack 且不執行；仍為 `processing` 且 lease 尚未到期時也不執行並直接 ack，由原 lease owner 負責後續結果；只有 lease expired 才可由該 delivery／recovery 依相同條件式 claim 規則接管。`BATCH#PROCESSING` recovery 也只在 lease expired 時重投。
9. worker 將失敗分成兩類：permanent validation/conflict 直接以條件式更新設 `batch_status=failed`、`BATCH#FAILED`、清除 `batch_lease_owner`／`batch_lease_until`、保存安全化 `batch_error`，然後 ack；retryable 錯誤不得先同步成 failed，而是 throw 讓 SQS retry/redrive。不得宣稱 broker redrive 會自動同步 DynamoDB。
10. DLQ reconciler Lambda 以 DLQ event source 消費重試耗盡的訊息，按訊息中的 `session_id + session_snapshot_hash` 強一致讀取 Base table；只有 snapshot hash 相符且 session 尚非 `completed` 時，才條件式收斂為 `batch_status=failed`／`BATCH#FAILED`、清 lease、寫安全化錯誤並告警。條件成功或已是相同 terminal 狀態後才視為處理成功，讓 event source 刪除 DLQ message；hash 不符等衝突不得誤改 session，須保留重試並告警。
11. 人工 replay 不依賴原 DLQ message，而是從 frozen session state 重建工作；必須先以 snapshot hash 等條件做 `failed→pending`、清錯誤／lease並設 `BATCH#PENDING`，成功後才重投。正常 worker 不得自行重開 failed，也不得以標記 completed 跳過失敗資料。

#### Direct Seven Pipeline

- `direct_seven` pipeline 不分塊、不檢索、不做 RAC 分類。對整個 session 的 frozen turns 依字元上限（`SEVEN_BATCH_CHAR_LIMIT`）於 turn 邊界貪婪分批，每批一次 LLM 呼叫萃取七大類事件。
- 萃取結果經 SharedTail 進行時間解析、canonical key 計算、slot 去重與型別驗證後寫入 events 表。
- pipeline 輸出的 `concept_id` 為 pseudo concept（`UCO.HighLevel.<type_id>`），不需要 UCO 細分類節點。
- retry 冪等性由 frozen turns（確定性輸入）與 conditional Put（確定性寫入）保證。

### `events` 表

#### Key 與欄位

| 名稱 | Key Schema | 用途 |
|---|---|---|
| Base table | PK `elder_id` + SK `event_id` | 事件 canonical identity |
| GSI `events-by-time` | PK `elder_id` + SK `event_time_key` | 時間軸；Projection `ALL` |

`event_time_key=<ts>#<event_id>`；後端先將 `ts` 正規化為台灣時區、固定毫秒精度的 ISO 8601 字串，再串接 `event_id`。字串排序即為時間排序，尾端 ID 可避免同一毫秒多事件的排序鍵碰撞。

MVP 不另建 `type` GSI：`GET /events` 先在 `events-by-time` 以 `elder_id` 與日期區間 Query，再以 `FilterExpression` 過濾 `type`。單一長者單日事件量低，此策略可避免額外索引儲存與寫入成本；未來需要跨長日期或高頻分類查詢時再評估分類索引。

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `elder_id`, `event_id`, `event_time_key` | String | 是 | `event_id=evt_<stable-hash(elder_id + canonical_event_key)>` |
| `canonical_event_key` | String | 是 | 跨 realtime/batch/manual 的 canonical 事件身分 |
| `extraction_track` | String | 是 | 首次建立來源：`realtime` \| `batch` \| `manual` |
| `ts` | String | 是 | 事件實際發生時間 |
| `type` | String | 是 | 高階類別：`diet` \| `activity` \| `sleep` \| `medication` \| `wellbeing` \| `safety` \| `other` |
| `concept_id` | String | 自動萃取事件必填 | 分類體系的細分類節點，如 `UCO.BehavioralRecord.MedicationBehavior.ScheduledMedication`；供後端摘要、統計與 alerts 篩選，API 不暴露 |
| `taxonomy_version` | String | 自動萃取事件必填 | 寫入當時的分類體系版本，如 `uco-1.0.0`；抽換體系後舊事件保留原值 |
| `detail` | String | 是 | canonical 事件的自然語言摘要描述 |
| `structured_detail` | Map | 否 | JSON 格式的結構化細節屬性（如 `{"medication_name": "血壓藥", "dosage": "一顆", "timing": "早上飯後"}`）；欄位結構因 `concept_id` 不同而異，API 不暴露 |
| `source` | String | 是 | `conversation` \| `manual` |
| `conversation_id` | String | 否 | 對話事件主要來源 turn；維持 API 契約 |
| `evidence_conversation_ids` | List[String] | 是 | 支持目前事件內容的來源 turn IDs |
| `session_id` | String | 否 | 對話來源 session |
| `source_chunk_id` | String | 否 | batch 首次建立事件時的來源 chunk；後續 evidence 可跨 chunk，但不改寫此初建來源 |
| `routine_id`, `routine_version`, `routine_date` | String/Number | 否 | routine 完成事件時必填 |
| `completed_by` | String | 否 | `conversation` \| `elder` \| `caregiver` |
| `confidence` | Number | 否 | 自動判定信心值 0–1 |
| `revision` | Number | 是 | 初始 `1`；合法 enrichment 條件式遞增 |
| `created_at`, `updated_at` | String | 是 | 首次建立與最近更新時間 |
| `schema_version` | Number | 是 | 初始 `1` |

#### 分類體系

事件分兩層分類：對外的**高階類別** `type`（前端與摘要使用，七類），以及對內的**細分類節點** `concept_id`（後端篩選、統計與 RAG 使用）。兩層都必須是可配置、可擴充、可抽換的資產，後端程式不硬編碼任何類別字串：

- 高階類別定義、細分類節點體系、以及「節點 → 高階類別」的映射各自是獨立的資產檔，隨部署包一起版控。
- 每筆事件寫入 `taxonomy_version` 記錄當時採用的體系版本；抽換或擴充體系只影響新建事件，舊事件保留原 `concept_id` 與 `taxonomy_version`。
- 未知或無法映射的節點退回 `type=other` 並告警，不得靜默丟棄。
- 可配置的邊界到 `daily_summaries.sections` 為止：`sections` 與 `type` 固定一一對應，新增高階類別必須同步 `sections`、`docs/api.md` 與摘要生成器。

`GET /events` 只用 `type` 過濾；`concept_id` 不對外暴露，MVP 也不為它另建索引，後端需要細分類篩選時在 Query 結果上以 `FilterExpression` 或程式端過濾。

#### Canonical identity 與寫入規則

- routine completion：canonical key 只由 logical occurrence 的 `elder_id + routine_id + routine_date` 決定；手動與對話完成、以及同日不同 routine version 都收斂到同一 event。`routine_version` 只記錄完成當下採用的有效版本，不參與 identity；存在該 canonical completion event 即判定 occurrence 為 `done`，否則依排程、目前時間與 grace period 動態衍生 `pending` 或 `missed`，routines 表不重複保存狀態。
  - `canonical_event_key` 的字串形式固定為 `routine_completion#<routine_id>#<routine_date>`，`routine_date` 為台灣日界的 `YYYY-MM-DD`；`elder_id` 於 `event_id` 推導時併入，不重複寫進 key。
  - 此格式與 `event_id` 推導方式一經寫入 event 即不可變更。變更會使同一 occurrence 算出不同 `event_id`，既有事件失去冪等收斂並產生重複完成紀錄。
- high-risk safety：canonical key 由 `SAFETY#alert_id` 決定。`alert_id` 由 `notify_caregiver` tool 在首次 `emergency` 時產生，並回傳給 Agent 以便後續 `critical_escalation`／`mitigation` 帶入同一 `alert_id`，讓同一警報情節的 emergency → escalation → mitigation 收斂到同一筆 event。Batch 做 safety enrichment 時可依 `evidence_conversation_ids` 或 `type=safety` 查詢既有事件，再以相同 canonical key 做 revision enrichment。
- normal event：canonical key 由 `Date + Slot + Subject + Predicate` 決定。
  - **Date**：`YYYY-MM-DD`，台灣日界（+08:00）。
  - **Slot**：固定邊界的時間桶，粒度由 `EVENT_SLOT_MINUTES` 設定（預設 30 分鐘）。例如 30 分鐘時：`SLOT_0900`（表示 09:00~09:29）、`SLOT_0930`（表示 09:30~09:59）；60 分鐘時：`SLOT_09`（表示 09:00~09:59）。計算公式：`slot_index = floor(minute_of_day / EVENT_SLOT_MINUTES)`，`slot_label` 依粒度決定格式。Slot 粒度一旦寫入 event 後不可變更；變更粒度只影響新建事件，舊事件保留原 Slot。
  - **Subject**：canonical 正規化主體，經 server-owned alias/normalization 收斂。
  - **Predicate**：合併原先 Action + Object 的單一語意謂語（如 `服用血壓藥`、`公園散步`），與 `concept_id` 是不同維度——`concept_id` 決定分類，Predicate 決定事件實例身分。它同樣經 server-owned lexicon／alias 收斂：萃取時只能從該分類的候選謂語中選取或回報「其他」，再由後端 alias map 與字形／語助詞正規化收斂，避免同一件事因表述不同（「吃血壓藥」與「服用降血壓藥」）產生兩筆事件。
  - 不使用 chunk、track、模型版本或描述文字決定 identity。
- 所有 `event_id` 都由 `elder_id + canonical_event_key` 穩定產生，與 chunk 無關。`source_chunk_id` 可記初建來源，但 `evidence_conversation_ids` 可跨 chunk 擴充。
- event identity 與既有事實欄位原則上不可覆寫；唯一可條件更新的例外是既有 safety event 的合法 enrichment。batch 以相同 event ID 與目前 `revision` 為條件，遞增 `revision` 並 enrich `detail`、`structured_detail`、`evidence_conversation_ids`、`confidence`、`updated_at`；不得重建事件，也不得因已存在就跳過補充資訊。
- batch 建立一般事件時使用 conditional Put。retry 命中完全相同 canonical event 視為冪等；若內容互斥則保留既有資料、記錄衝突並讓工作失敗／告警，不靜默覆寫。
- batch 萃取到疑似 routine 完成時，仍只寫一般事件，並在 `structured_detail` 記 `suspected_routine_id` 供摘要層降噪；不得寫 canonical completion event，也不改 routine 狀態。completion event 只能由對話大腦的 tool calling 或照護者手動完成端點建立。
- `GET /events` 一律 Query `events-by-time`；日期邊界以台灣時間計算，`ScanIndexForward=false` 回最新事件優先，`type` 以 FilterExpression 過濾，分頁將 DynamoDB `LastEvaluatedKey` 編碼為不透明 `next_token`。
- `detail` 保存足以供時間軸、摘要與語音查詢使用的完整事件描述，但不複製逐字稿；`structured_detail` 保存結構化細節供後端摘要生成、統計與 RAG 使用。需要追溯原文時，依主要 `conversation_id` 或 `evidence_conversation_ids` 回 conversations 讀取，以減少 PII 重複儲存。
- `GET /events` 只公開既有 API 欄位，不暴露 canonical key、track、chunk、revision 或 `structured_detail`。canonical 規則降低重複機率，但不宣稱 zero duplicate 或 100% extraction accuracy。
- events 不設定 TTL；資料依 PII 政策保留／刪除，DynamoDB 啟用 AWS owned key 靜態加密與 Point-in-Time Recovery。

#### Batch 記憶體內去重 (In-Memory Deduplication)

- Session 關閉後的 Batch 階段，Session 的所有對話已凍結（Snapshot），Batch Worker 先在記憶體中將「前後 30 分鐘內重複講到的事件」進行合併與洗牌，最後只產出一筆標準化事件，並算出一組固定的 Canonical Key 寫入 DynamoDB。
- 因為 Batch 處理的 input 是 immutable frozen snapshot，記憶體內操作是確定性的（deterministic），retry 冪等。
- 合併規則：同一 Subject + Predicate 且時間差在 `EVENT_SLOT_MINUTES`（預設 30 分鐘）內的事件收斂為同一筆；`detail` 與 `structured_detail` 取最完整的一次，`evidence_conversation_ids` 聯集所有來源 turn。
- 去重後的標準化事件以 conditional Put 寫入 DynamoDB；跨 Session 的相同 canonical key 依已有 conditional Put 規則處理。


### `daily_summaries` 表

Base table：PK `elder_id` + SK `date` (`YYYY-MM-DD`，台灣日界)。

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `elder_id`, `date` | String | 是 | 長者與日期 |
| `overview` | String | 是 | 當日總覽 |
| `sections` | Map | 是 | 固定 `diet/activity/sleep/medication/wellbeing/safety/other`，與 events `type` 一一對應；值可為 Null |
| `routines` | Map | 是 | `completed`, `missed`, `items[{routine_id,title,status}]` |
| `alerts` | List[String] | 是 | 無警訊為 `[]` |
| `interaction_count` | Number | 是 | 當日 `/chat` turn 數 |
| `data_status` | String | 是 | `complete` \| `partial` |
| `pending_session_count` | Number | 是 | 生成時尚可能缺少 batch materialization 的相關 sessions 數 |
| `input_through_at` | String | 是 | 本摘要承諾納入來源資料的時間上限 |
| `generated_at` | String | 是 | 生成完成時間 |
| `completeness_rank` | Number | 是 | `complete=1`、`partial=0`；覆寫規則需要可比較的完整度 |
| `generator_version`, `schema_version` | String/Number | 是 | pipeline/schema 版本 |

`input_through_at`、`completeness_rank`、`generator_version`、`schema_version` 是後端內部欄位；API 在既有摘要欄位之外公開新增 `data_status` 與 `pending_session_count`。`completeness_rank` 存在的唯一理由是讓「同一 cutoff 下 `complete` 優先於 `partial`」成為條件式寫入可比較的條件，而不是讀後判斷再寫；並行的排程與手動生成因此不會互相覆蓋。

- 生成前必須檢查摘要日期內有 turn 的 session：`active`、`closing`，或 `closed` 但 `batch_status=pending|processing|failed` 都計入 `pending_session_count`。只有 `pending_session_count=0`，且所有相關 closed session 的 batch 均為 `completed`，`data_status` 才能是 `complete`；否則為 `partial`。
- `routines.items` 每個 `routine_id + date` 最多一項，固定為 `{routine_id,title,status}`。每筆 occurrence 的 `occurrence_cutoff=min(input_through_at, routine_date 的台灣日界結束 23:59:59.999+08:00)`；canonical completion event 已存在時，`status=done`，`title` 優先取 event 所記 `routine_version` 的不可變定義，完成資料取該 event，不受 cutoff 後或同日後續版本影響；未完成時才以 `occurrence_cutoff` 前最新有效版本衍生唯一 occurrence。`routines.completed` 計算 `status=done`，`routines.missed` 計算 `status=missed`；pending 不納入兩者，摘要不回寫 routine 狀態。
- 摘要 generator 有兩個觸發來源：EventBridge 排程與 `POST /summaries/generate` 手動請求；兩者使用同一生成與條件覆寫規則，並以 `generator_version` 記錄 pipeline 來源版本。
- 排程摘要會等待相關 closed sessions 的 batch 完成，直到設定的等待窗口。超過窗口仍未完成時寫 `partial`；後續 batch 全部完成後排程重算，覆寫為 `complete`。手動 `POST /summaries/generate` 不等待完整窗口，可合法產生 `partial`。
- 摘要輸入區間固定為 `[date 00:00:00+08:00, input_through_at]`。同日重建以 `input_through_at` 為 cutoff；較舊 cutoff 不得覆寫較新 cutoff。同一 cutoff 先以完整度排序，`complete` 優先於 `partial`；完整度相同才以 `generated_at` 較新者勝出。
- `GET /summaries` 以 `elder_id` Query Base table，`date` 依台灣日界形成 `from..to` sort-key 範圍並倒序分頁；只回已生成日期。
- `sections` 七個 key 每次完整寫入，無資料為 Null；alerts 可參考近期事件與 `type=safety` 事件標記跨日趨勢。摘要是衍生快照，不是 event 或 routine 狀態的真理來源。
- daily_summaries 不設定 TTL，啟用靜態加密與 Point-in-Time Recovery。

### `routines` 表

| 名稱 | Key Schema | 用途 |
|---|---|---|
| Base table | PK `routine_id` + SK `version` | 不可變 routine 版本 |
| GSI `routines-current-by-elder` | PK `elder_id` + SK `current_sort_key` | sparse current 定義 |
| GSI `routine-versions-by-elder` | PK `elder_id` + SK `version_time_key` | 歷史有效版本 |

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `routine_id`, `version`, `elder_id` | String/Number | 是 | 穩定業務 ID、版本、長者；照護者建立時由 scoped request 穩定產生 |
| `current_sort_key` | String | 否 | 僅 `is_current=true` 版本存在；`A#<created_at>#<routine_id>` 表 active，`I#<created_at>#<routine_id>` 表 inactive |
| `version_time_key` | String | 是 | `<effective_from>#<routine_id>#<version>`；`effective_from` 固定毫秒與 `+08:00`，依長者查歷史有效版本 |
| `is_current`, `active`, `remind` | Boolean | 是 | current、啟用、提醒 |
| `effective_from`, `effective_to` | String | 是/否 | 版本有效區間 |
| `title` | String | 是 | 顯示名稱 |
| `type` | String | 是 | 與 events 相同的高階類別 |
| `schedule` | Map | 是 | discriminated union：`daily{freq,time}`、`weekly{freq,weekday,time}`、`once{freq,date,time}` |
| `created_by`, `updated_by` | String | 是 | `caregiver` \| `conversation` |
| `created_by_id`, `updated_by_id` | String | 否 | caregiver Cognito `sub` |
| `source_conversation_id`, `source_session_id` | String | 否 | 對話來源追溯 |
| `canonical_action_key` | String | 否 | realtime conversation action 的穩定身分 |
| `change_request_id`, `request_hash` | String | 是 | scoped 冪等鍵與正規化 payload hash；照護者來源含 authenticated actor `sub` |
| `created_at`, `updated_at` | String | 是 | 建立與版本時間 |
| `schema_version` | Number | 是 | 初始 `1` |

- `routine_id` 不隨排程更正而變。`schedule.freq` 是 discriminator：`daily` 只帶 `time`；`weekly` 帶單一 `weekday`（1–7，週一為 1）與 `time`；`once` 帶 `date` 與 `time`。每週多日必須拆成多筆 weekly routine，不在單筆保存 weekday list。
- conversation action 明確區分 `create/update/delete/complete`，由 Bedrock Agent tool calling 在 `InvokeAgent` 回應前同步處理；batch 不得建立、修改、刪除或完成 routine。`complete` 只寫 canonical event，不建立 routine 新版本。
- 對話 action 的 `canonical_action_key` 由 `elder_id + source_conversation_id + action + canonical target` 穩定產生，`change_request_id` 由該 key 派生並搭配 `request_hash`。相同 key/hash 重播既有結果，不重複建立版本或 completion event；相同 key 搭配不同 hash 必須視為衝突並告警，模型文字或 chunk ID 不得作為身分。
- 照護者 `POST /routines` 的 `routine_id=stable-hash(elder_id + authenticated actor sub + client_request_id)`（加 `rtn_` 前綴），`version=1`；`change_request_id` 使用同一 `elder_id + actor sub + client_request_id` scope 並保存正規化 `request_hash`。以 conditional Put／transaction 建立；並行同 scoped ID 且同 hash 回相同強一致結果，不同 hash 回 idempotency conflict。
- 照護者 `PATCH` 與 `DELETE` 的 `change_request_id` scope 固定為 `routine_id + authenticated actor sub + client_request_id`，只接受公開白名單欄位；以 `TransactWrite` 在同一 transaction 驗證 scoped request hash、條件式確認舊版仍為 current、關閉舊版（`is_current=false`、設定 `effective_to`、移除 `current_sort_key`）及新增下一版（刪除時 `active=false`），不可改 `routine_id/elder_id/created_at/created_by`。並行同 scope/hash 只產生一個 next version 並回相同結果；不同 hash 衝突。
- `current_sort_key` 只存在於 current 版本；active 使用 `A#...`，刪除後的新 current 版本使用 `I#...`，歷史版本一律移除該欄位。定義列表從 `routines-current-by-elder` 查 `A#` 範圍；current GSI 最終一致，建立／修改／刪除 API 直接從 Base table 強一致讀回。

- occurrence 的 logical identity 固定為 `elder_id + routine_id + routine_date`；每個 `routine_id` 在每個台灣日期最多一筆 logical occurrence，`daily`、`weekly`、`once` 因而每日也都最多一次。歷史解析一律先算 `occurrence_cutoff=min(query_or_summary_cutoff, routine_date 的台灣日界結束 23:59:59.999+08:00)`。若 canonical completion event 已存在，occurrence 固定為 `done`，顯示用的 `title`、`type`、`scheduled_at` 等定義資料優先取 event 記錄之 `routine_version` 對應的不可變版本，`completed_at`、`completed_by` 等完成資料取該 event；即使同日後續改版也保留完成當時結果。只有未完成 occurrence 才使用 `occurrence_cutoff` 前最新有效版本，同日 cutoff 前的新版本可 supersede 舊 schedule 且不展開第二筆。
- 指定日期展開行程時，Query `routine-versions-by-elder` 取得 `effective_from` 不晚於 `occurrence_cutoff` 的候選，再按上述 completion-first 規則與 logical identity 收斂。歷史日期一旦越過該台灣日界結束，`occurrence_cutoff` 即封頂於該日，後續新版本不得 retroactively 改寫該日 occurrence；不得拿 current 定義回推歷史日期。
- routines 不保存 occurrence 的 `done/pending/missed`。`done` 由 `elder_id + routine_id + routine_date` 的 canonical completion event 判定；event 的 `routine_version` 只記錄完成採用版本。超過唯一 occurrence 的 `scheduled_at + ROUTINE_GRACE_MINUTES`（預設 120）且未完成才是 missed。
- App 在 mutation 後若立刻重查 current GSI，後端／client 依 200、500、1000 ms 指數退避最多重試三次；仍未可見時以 mutation response 的強一致結果為準，不得把舊 GSI 結果誤判為寫入失敗。
- routines 不設定 TTL；歷史版本依 PII 政策保存，DynamoDB 啟用 AWS owned key 靜態加密與 Point-in-Time Recovery。

## Session／Chunk 與資料邊界

- **Session** 是 immutable input snapshot 的邊界：active 接納 turns，closing freeze/verify，closed 固定輸入並啟動離線 materialization。closed 先於 batch。
- **Tool calling ownership**（取代 realtime rail）：chat 回覆、routine create/update/deactivate/complete，以及潛在高風險 safety events。它透過對話大腦的 tool calling 同步寫入 routine completion event 與 safety event，不追求一般事件完整萃取。
- **Batch ownership**：closed snapshot 的 normal events（含記憶體內去重）、既有 safety event enrichment、chunk manifest 與 batch 狀態。它不改 routine，也不改 frozen turn/session input；萃取到疑似 routine 完成時只以 `structured_detail.suspected_routine_id` 標記。
- **Chunk** 只是同一 closed session 的 static processing range，不是公開 API 資源或資料身分。core ranges 完整且不重疊，context overlap 不 emit。
- **Events** 是實際發生與 routine 完成的 canonical 紀錄；**routines** 是計畫；**daily_summaries** 是具 `data_status` 的衍生快照。長期記憶由 AWS AgentCore 管理。

## 成本與可觀測性

移除每輪額外完整 extraction 模型呼叫，預期可降低模型呼叫與 realtime latency 成本，但實際效果必須以 telemetry 驗證。至少觀測 chat latency、structured output 失敗率、safety rule 命中、每 session turn/input bytes、batch attempts、SQS duplicate/DLQ、partial summary 比例與重算延遲、去重合併率、`type`／`concept_id` 分佈；未量測前不承諾固定百分比節省。

## 後端環境變數

extraction 相關行為一律由環境變數驅動，不寫死在程式碼：

| 變數 | 用途 |
|---|---|
| `EVENT_SLOT_MINUTES` | canonical key 的 Slot 粒度，預設 30 |
| `ROUTINE_GRACE_MINUTES` | occurrence 由 pending 轉 missed 的寬限，預設 120 |
| `SESSION_MAX_TURNS`、`SESSION_MAX_INFLIGHT_TURNS`、`SESSION_MAX_INPUT_BYTES` | session 容量上限與自動 close 門檻 |
| `TAXONOMY_VERSION` | 寫入 event 的分類體系版本 |
| `CHUNKER_TYPE` | 分塊策略 |
| `EXTRACTION_MODE`、`DISAGGREGATION_MODE` | 萃取的 schema 約束與分裂策略 |
| `RAC_TOP_K` | 候選細分類節點數 |
| `BEDROCK_MODEL_ID` | 對話模型（Converse modelId 或 inference profile）；預設為 Anthropic 在 Bedrock 的旗艦模型加 global cross-Region inference profile |
| `BEDROCK_CLASSIFIER_MODEL_ID`、`BEDROCK_EXTRACTOR_MODEL_ID`、`BEDROCK_CHUNKER_MODEL_ID` | 分階段覆寫；留空沿用主模型。分類與分塊的 schema 固定、輸出短，可換較便宜的模型；萃取是品質瓶頸不建議降級 |
| `EMBEDDING_MODEL_ID`、`EMBEDDING_DIM`、`CONCEPT_VECTOR_INDEX`、`CONCEPT_VECTOR_BUCKET` | embedding 供應者、維度與概念向量索引 |
| `CHUNK_PLANNER_VERSION`、`BATCH_EXTRACTOR_VERSION` | 寫入 session／turn 的版本戳記 |
| `METRICS_NAMESPACE`、`METRICS_ENABLED` | EMF 指標的 namespace 與開關；指標寫 stdout 由 CloudWatch Logs 解析，不需額外 IAM |
| `BATCH_LEASE_SECONDS`、`SESSION_IDLE_MINUTES`、`SESSION_SWEEP_LIMIT` | batch lease 長度、idle close 門檻與單次 sweep 上限 |
| `REQUEST_LEASE_SECONDS` | `/chat` turn 的 request lease 長度；必須大於 chat Lambda 的 timeout |
| `BATCH_ALERT_TOPIC_ARN` | batch 收斂為 `failed` 時的告警 topic |
| `SUMMARY_GENERATOR_VERSION` | 寫入 `daily_summaries.generator_version` 的版本戳記 |
| `BEDROCK_SUMMARY_MODEL_ID` | 摘要階段模型覆寫；留空沿用主模型 |
| `SUMMARY_ALERT_LOOKBACK_DAYS`、`SUMMARY_MAX_EVENTS` | alerts 的跨日觀察窗與進 prompt 的事件數上限 |
| `SUMMARY_WAIT_MINUTES`、`SUMMARY_BACKFILL_DAYS`、`SUMMARY_SWEEP_LIMIT` | partial 重算的等待窗口、backfill 掃描天數與單次 sweep 長者數上限 |
| `AGENTCORE_RUNTIME_ARN`、`AGENTCORE_ENDPOINT_NAME` | chat Lambda 呼叫對話大腦的位址；留空時 `/chat` 走模擬回覆，供本機開發 |
| `AGENT_MODEL_ID` | 對話大腦的模型覆寫；留空沿用 `BEDROCK_MODEL_ID` |
| `TOOLS_FUNCTION_NAME`、`AGENT_MEMORY_ID`、`KNOWLEDGE_BASE_ID`、`KB_RETRIEVE_TOP_K` | AgentCore Runtime 內部使用：工具箱 Lambda、託管記憶、衛教知識庫與單次檢索段落數 |
| `MAX_TOOL_ITERATIONS` | 單輪對話的工具呼叫上限；防模型繞圈把 chat Lambda 的 timeout 耗盡 |

## Repo 結構

```text
e-hakka-care/
├── .kiro/          # Kiro 設定與 specs
├── app/            # Flutter
├── asr-lambda/     # SageMaker inference container 開發文件與本機 conda 環境
├── backend/        # Python Lambda handlers、ASR/TTS 領域模組與 extraction pipeline
│   └── src/shared/       # ASR/TTS 與其他跨 handler 共用模組
├── terraform/      # AWS IaC
├── data/           # 模擬 persona、腳本、知識文件
├── docs/           # 架構、API、ASR、ADR、旅程、PII
└── README.md
```

`backend/src/` 下 `handlers/` 是 API 與事件入口、`shared/` 是跨 handler 共用層、`extraction/` 是生活記錄的萃取 pipeline（`direct_seven`：不分塊、不檢索，依 turn 邊界分批做七大類事件萃取，經 SharedTail 完成 canonical identity 與 slot 去重），只由 batch 相關 Lambda 使用；`agentcore_runtime/` 是對話大腦，唯一不跑在 Lambda 上的部分，以 zip 部署到 AgentCore Runtime。

## Verification

- **Routine 秒級可見／occurrence 冪等**：驗證對話中的 routine 建立、修改、停用與完成在 `/chat` response 前原子提交，`routines_updated` 準確；照護者 POST/PATCH 並行同 scoped request 收斂；同 routine/date 改版仍只顯示一筆 occurrence，completion 不重複。
- **一般事件 close 後產生**：一般 diet/activity/sleep/medication/wellbeing/safety/other 事件不在 realtime 寫入，close 並完成 batch 後才 materialize；例外只有潛在高風險 safety event，由 tool calling（`notify_caregiver`）先建立、batch 再 enrich。
- **分類體系可配置**：抽換高階類別定義或節點映射資產後，新事件依新體系寫入且 `taxonomy_version` 隨之改變，舊事件不受影響；未知節點退回 `other` 並告警。
- **batch 不寫 routine completion**：batch 萃取到疑似 routine 完成時只寫一般事件並標記 `suspected_routine_id`，occurrence 仍依 canonical completion event 判定。
- **Batch 去重**：batch worker 先在記憶體內依 `EVENT_SLOT_MINUTES` 去重，再以 conditional Put 寫入；retry 冪等。
- **Tool calling safety**：對話大腦在回應 chat Lambda 之前透過 `notify_caregiver` tool 同步建立 `type=safety` 的 event；batch 同 key 只做 revision enrichment。
- **Close immutable／inflight recovery**：驗證 `/chat` reserve 與 close race、inflight 回 409、lease-expired turn 接管或安全失敗移除 reservation、`active→closing→closed`，以及 closed 後無法追加或修改 frozen turns、ordered IDs、counts 與 snapshot hash。
- **Batch retry 冪等**：SQS retry、duplicate delivery 與 DLQ replay 時，pipeline 輸出依 frozen turns 與 snapshot hash 確定性產出相同 canonical key，conditional Put 確保事件不重複。
- **SQS duplicate／DLQ／recovery**：模擬 closed 後 SendMessage 前中斷，由 `BATCH#PENDING` sweep 重投；processing lease 尚有效的相同 session/hash duplicate 不執行並直接 ack、由原 owner 收斂，僅 lease expired 可由 delivery／recovery 接管；failed/completed duplicate ack 不執行；retryable redrive 不假設同步 DDB；DLQ reconciler 依 session/hash 收斂 failed、清 lease與告警，人工 replay 先做 failed→pending 並從 frozen state/manifest 重建。
- **Cross-track safety enrichment**：tool calling 先建 safety event，batch 以相同 canonical key 條件式增加 revision、detail/evidence/confidence，確認 event ID 不變且 evidence 可跨 chunk。
- **摘要 partial→complete**：有 active/closing 或 batch pending/processing/failed session 時為 `partial`；等待窗口後可先寫 partial，相關 batch 完成後重算為 `complete`，相同或較舊 input cutoff 的 partial 不得蓋掉 complete。
- **資料契約**：turn 雙軌欄位、session/batch enum、event identity、summary `data_status` 與 `docs/api.md` 一致；API 不暴露 extraction internals。長期記憶由 AWS AgentCore 管理，不在 DynamoDB 資料契約範圍內。
- **端到端**：Android 完成中文免手持迴圈、明確 close、batch 收斂、照護者事件／摘要顯示；客語於第二階段以測試音檔驗證。
- 後端單元、整合測試（pytest），並以 LocalStack／測試環境覆蓋 DynamoDB 條件式寫入、transaction、SQS retry/DLQ 與 EventBridge recovery。
