# 每日摘要（Module B）實作計畫

實作 `daily_summaries` 表與摘要功能。權威規範以 [framework.md](framework.md) 為最高原則，
API 契約以 [api.md](api.md) 為準；本文件只描述實作決策與步驟，不覆寫兩者。

## 1. 現況與落差

| 項目 | 現況 |
|---|---|
| `daily_summaries` DynamoDB 表 | terraform 已建（PK `elder_id` + SK `date`，PITR，無 GSI） |
| `db.put_daily_summary` | 無條件 `put_item`，沒有 api.md 要求的 cutoff／完整度比較，且未走 `prepare_item` |
| `db.get_daily_summaries` | 有日期範圍 Query，但正序、無 `limit`／`next_token` |
| `handlers/summaries.py`、`handlers/summary_generator.py` | 皆為 stub |
| `pending_session_count` 所需查詢 | 不存在：沒有「依日期找相關 session」或「當日 turn 數」的資料層函式 |
| routines occurrence 快照 | `db.get_daily_routines` 是舊實作：`scan` + 單一 key，沒有 `occurrence_cutoff`、沒有 routine 版本解析，與 routines 表的 `routine_id + version` 複合鍵不一致 |
| terraform | 沒有 summary Lambda、沒有 EventBridge 排程、`/summaries` 路由未接、IAM 缺 `daily_summaries` 與 `routines` 權限 |

摘要是**衍生快照**，不是 events 或 routine 狀態的真理來源。這條原則決定了下面每個設計：
摘要永不回寫 event、永不回寫 routine 狀態，重算一律從來源資料重新算。

## 2. 元件與資料流

```
EventBridge nightly ─┐
EventBridge backfill ─┼→ summary_generator Lambda ─┐
POST /summaries/generate ─┘                        ├→ shared/summarizer.py
                                                   │     ├ db: 當日 events（events-by-time）
                                                   │     ├ db: 當日 turns（conversations-by-time）
                                                   │     ├ sessions: 相關 session 強一致狀態
                                                   │     ├ shared/routines.py: occurrence 快照
                                                   │     └ bedrock.converse_json: overview + sections + alerts
                                                   └→ db.put_daily_summary（條件式覆寫）

GET /summaries ─→ api_summaries Lambda ─→ db.list_daily_summaries（倒序分頁）
```

生成邏輯只有一份（`shared/summarizer.py`），排程與手動端點共用；兩者的差別只有兩個參數：
`input_through_at`（cutoff）與是否套用等待窗口。放 `shared/` 而非新套件，是因為它就是
「跨 handler 共用層」的定義，且不需要改動 framework 的 repo 結構章節。

## 3. `pending_session_count` 與 `data_status`

契約：日期範圍內有 turn 且仍 `active`／`closing`，或 `closed` 但 batch 為
`pending`／`processing`／`failed` 的 session 數。`pending_session_count=0` 且相關 closed
session 的 batch 全部 `completed` 才可為 `complete`。

查詢路徑（決策）：

1. Query GSI `conversations-by-time`（PK `elder_id`、SK `conversation_time_key`），範圍
   `[day_start(date), day_end(date)#\uffff]`，撈完該日所有 turn。這一次 Query 同時給出
   `interaction_count`（`request_status=completed` 的 turn 數）與相關 `session_id` 集合。
2. 對每個 distinct `session_id` 回 Base table 做 `ConsistentRead` 讀取 session metadata，
   再判斷 state 與 `batch_status`。

為什麼不直接用 `sessions-by-state` GSI：那個 sparse index 的 PK 是狀態、SK 是時間，查得到
「目前 pending 的 session」但查不到「某一天有 turn 的 session」，而且 framework 明文寫
「GSI 只用來找候選，不能當成 freeze、snapshot 或 ownership 判斷的真理來源」。因此候選由
GSI 找、狀態一律回 Base table 強一致讀。

`interaction_count` 只計 `request_status=completed` 的 turn：failed turn 保證沒有業務副作用，
把它算進互動次數會讓照護者看到不存在的互動。

## 4. 覆寫規則：`input_through_at` 與 `completeness_rank`

api.md 的三層優先序：較舊 cutoff 不得覆寫較新 → 同 cutoff `complete` 優先於 `partial`
→ 完整度相同才比 `generated_at`。這必須是**條件式寫入**，不是讀後判斷再寫：排程與手動
生成會並行，讀後判斷會讓較舊的結果覆蓋較新的。

DynamoDB 的 `ConditionExpression` 只能比較屬性值，因此完整度要有數值表示：新增後端內部
欄位 `completeness_rank`（`complete=1`、`partial=0`）。

```
attribute_not_exists(elder_id)
OR input_through_at < :cutoff
OR (input_through_at = :cutoff AND completeness_rank < :rank)
OR (input_through_at = :cutoff AND completeness_rank = :rank AND generated_at <= :generated_at)
```

不用「`complete` 的字典序小於 `partial`」這個巧合來省一個欄位：那種寫法能跑，但下一個人
新增第三種狀態時會安靜地錯。條件不成立時回既有摘要並記 log，不視為錯誤——那正是規則
生效的樣子（例如手動 partial 想蓋掉排程 complete）。

## 5. routines occurrence 快照

`shared/routines.py` 新實作，供摘要與未來的 `GET /routines` 共用。規則完全依 api.md：

1. `occurrence_cutoff = min(input_through_at, day_end(date))`。
2. **completion-first**：先看 `elder_id + routine_id + routine_date` 的 canonical completion
   event 是否存在。存在即 `done`，`title`／`type`／`scheduled_at` 取 event 所記
   `routine_version` 對應的不可變版本（`GetItem routines{routine_id, version}`），
   `completed_at`／`completed_by` 取該 event。同日後續改版不影響已完成的結果。
3. 未完成才展開排程：Query `routine-versions-by-elder` 取 `effective_from <= occurrence_cutoff`
   的候選，依 `routine_id` 分組取最新有效版本，判斷該日是否排程（`daily`／`weekly.weekday`／
   `once.date`），每個 `routine_id + date` 最多一筆 occurrence。
4. `scheduled_at + ROUTINE_GRACE_MINUTES` 早於 `occurrence_cutoff` 且未完成 → `missed`，
   否則 `pending`。`completed` 與 `missed` 各自計數，`pending` 不計入任何一邊。

舊的 `db.get_daily_routines` 不再供摘要使用（它假設 routines 表只有 `routine_id` 一個 key，
在真實 schema 上讀不到東西），保留但標為 deprecated，由未來的 routines handler 一併移除。

## 6. 摘要文字生成

模型只負責自然語言，不負責任何可計算的事實：

| 由程式決定 | 由模型決定 |
|---|---|
| `interaction_count`、`pending_session_count`、`data_status`、`completeness_rank` | `overview` |
| `routines.completed`／`missed`／`items`（含 status） | `sections` 七類文字 |
| `date`、`input_through_at`、`generated_at`、版本戳記 | `alerts` 文字 |

理由：這些數字有唯一正確答案且可驗證，交給模型只會引入無法重現的錯誤。反過來，
自然語言摘要沒有唯一答案，適合模型。

- 輸入：當日 events 的 `detail` 全文（不是只看 `type`）、`structured_detail`、`type`，
  加 routines occurrence 快照，以及近 `SUMMARY_ALERT_LOOKBACK_DAYS` 天的 `safety`／
  `wellbeing` 事件摘要行（供跨日趨勢 alerts）。
- 輸出 schema：`additionalProperties: false`，`overview: string`、七個 section key 為
  `string | null`、`alerts: string[]`。七個 key 每次完整出現，無資料為 `null`。
- 當日沒有任何 event 也沒有 routine occurrence 時**不呼叫模型**：七類為 `null`、
  `alerts=[]`、`overview` 用固定字串。省成本，也避免模型從空輸入編故事。
- `sections` 的 key 一律取自 `models.SUMMARY_SECTION_KEYS`（= `EventType`），不寫死字串；
  新增高階類別時 sections 自動跟上。
- 模型 ID 走既有分階段覆寫慣例：`BEDROCK_SUMMARY_MODEL_ID`，留空沿用 `BEDROCK_MODEL_ID`。
- 逐字稿不進 prompt：只給 event 的 `detail`／`structured_detail`。減少 PII 擴散面，也符合
  framework「不複製逐字稿」的原則。

## 7. 觸發與等待窗口

| 觸發 | 行為 |
|---|---|
| EventBridge nightly（`cron` 台灣時間 23:50） | 對所有長者生成當日摘要；有 pending session 就寫 `partial` |
| EventBridge backfill（`rate(SUMMARY_BACKFILL_MINUTES)`） | 掃近 `SUMMARY_BACKFILL_DAYS` 天內 `data_status=partial` 的摘要重算；只在等待窗口內重算 |
| `POST /summaries/generate` | 同步生成，不等窗口，可合法回 `partial` |

等待窗口的實作：backfill 只處理 `generated_at` 距今在 `SUMMARY_WAIT_MINUTES` 內的 partial
摘要。超過窗口就停止重算——batch 若真的卡在 `failed`，那是 DLQ reconciler 與告警的責任，
不該讓摘要無限重算燒模型費用。

長者清單來源：`elders` 表 `scan`（MVP 表小），單次 sweep 上限 `SUMMARY_SWEEP_LIMIT`。
`daily_summaries` 不為 `data_status` 另建 GSI：單一長者每天一筆，近幾天的資料用
Base table Query 就夠，加索引的儲存與寫入成本換不到東西。

## 8. API 落地

- `GET /summaries?elder_id=&from=&to=&limit=&next_token=`：`from`／`to` 預設最近 7 天，
  日界台灣時間，倒序（最新日期優先），分頁用既有 `encode_next_token`。回應以**白名單**
  投影，`input_through_at`、`completeness_rank`、`generator_version`、`schema_version`
  都不外流。
- `POST /summaries/generate`：body `{elder_id, date?}`，`date` 預設今天，同步生成，
  200 回單一摘要物件（結構同列表 item）。
- 授權：兩者都走 `auth.assert_can_access_elder`（長者只能看自己、照護者依綁定）。
- 錯誤：日期格式、`from > to`、`limit` 範圍 → 400 `INVALID_PARAMETER`；
  `next_token` 解不開 → 400；其餘 → 500 `INTERNAL_ERROR`，沿用 events handler 的映射方式。
- 兩個端點掛同一支 `api_summaries` Lambda，依 `httpMethod` 與 resource path 分派。

## 9. 新增環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `SUMMARY_GENERATOR_VERSION` | `summary-generator-1` | 寫入 `generator_version` |
| `BEDROCK_SUMMARY_MODEL_ID` | 空（沿用主模型） | 摘要階段模型覆寫 |
| `SUMMARY_ALERT_LOOKBACK_DAYS` | `7` | alerts 的跨日觀察窗 |
| `SUMMARY_MAX_EVENTS` | `120` | 進 prompt 的事件數上限，防單日爆量 |
| `SUMMARY_WAIT_MINUTES` | `180` | partial 重算的等待窗口 |
| `SUMMARY_BACKFILL_DAYS` | `2` | backfill 掃幾天內的摘要 |
| `SUMMARY_SWEEP_LIMIT` | `50` | 單次 sweep 處理的長者數上限 |

## 10. 觀測指標

沿用 `shared/metrics.py` 的 EMF（寫 stdout，不需額外 IAM）：摘要生成次數、
`partial`／`complete` 比例、pending session 數、重算次數、模型延遲與失敗率、
被覆寫規則擋下的次數。`partial` 比例與重算延遲是 framework 成本章節明列要觀測的項目。

## 11. 任務拆解

| # | 內容 | Commit | 狀態 |
|---|---|---|---|
| 1 | 本文件與 framework 欄位／env 同步 | `docs(summaries): plan daily summarization` | 完成 |
| 2 | 資料層：條件式覆寫、倒序分頁、當日 turn／session 盤點 | `feat(summaries): add daily summary data layer` | 完成 |
| 3 | `shared/routines.py` occurrence 衍生 | `feat(routines): derive occurrences with cutoff and versions` | 完成 |
| 4 | `shared/summarizer.py` 生成邏輯 | `feat(summaries): generate daily summary content` | 完成 |
| 5 | `GET /summaries`、`POST /summaries/generate` | `feat(summaries): implement summary api` | 完成 |
| 6 | 排程 handler（nightly + backfill） | `feat(summaries): add scheduled generator` | 完成 |
| 7 | terraform：Lambda、EventBridge、IAM、路由 | `chore(terraform): wire summary lambdas and routes` | 完成 |
| 8 | 整合驗收（moto，只有模型是假的） | `test(summaries): cover partial to complete recovery` | 完成 |

實作後的檔案位置：

| 檔案 | 內容 |
|---|---|
| `backend/src/shared/db.py` | `put_daily_summary`（覆寫優先序）、`get_daily_summary`、`list_daily_summaries`、`list_turns_by_day` |
| `backend/src/shared/sessions.py` | `is_pending_materialization`、`list_pending_sessions` |
| `backend/src/shared/routines.py` | occurrence 衍生與 `summary_snapshot` |
| `backend/src/shared/summarizer.py` | `build_summary`、`generate_and_store`、prompt 與 schema |
| `backend/src/handlers/summaries.py` | `GET /summaries`、`POST /summaries/generate` |
| `backend/src/handlers/summary_generator.py` | nightly 與 backfill 兩種排程 mode |
| `terraform/{lambda,eventbridge,api_gateway,cloudwatch,variables,outputs}.tf` | 兩支 Lambda、兩條排程、IAM、路由、告警 |
| `backend/tests/test_summar*.py`、`test_routine_occurrences.py`、`test_session_pending.py` | 單元與整合測試 |

## 12. Verification 對照

- **partial → complete**：有 active／closing 或 batch pending／processing／failed 的 session
  時為 `partial`；相關 batch 完成後重算為 `complete`；相同或較舊 cutoff 的 partial 不得
  蓋掉 complete（以條件式寫入驗證，不靠讀後判斷）。
- **routines 快照**：同日改版仍只有一筆 occurrence；已完成的 occurrence 取完成當時版本的
  定義；`pending` 不計入 `completed`／`missed`。
- **sections 契約**：七個 key 每次完整回傳、無資料為 `null`，key 集合等於 `EventType`。
- **不外流內部欄位**：回應不含 `input_through_at`、`completeness_rank`、`generator_version`、
  `schema_version`。
- **無資料日**：無對話且無待處理 session 時七類為 `null`、`alerts=[]`、
  `interaction_count=0`、`pending_session_count=0`，且可為 `complete`，且不呼叫模型。
