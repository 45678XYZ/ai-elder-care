# Design：main 已完成產品狀態

## 1. 設計定位與基準

本文件是 `main-completed-product-state` 的 consolidated design，用來描述 Git `main` commit `7fce81c7e8669ba805541a98fbc0646db9328ac7` 所代表的既有產品組成、資料邊界、寫入 ownership 與驗證證據。它不是新功能設計，也不是 implementation plan；不以工作樹中的 `eval/`、其他分支或未合併 agent 變更作為完成依據。

設計的「完成」採證據索引語意：source module、Terraform、docs、data fixture 與既有測試在指定 main commit 中存在且互相對應，即構成已完成狀態的證據。本階段不重新執行產品測試、不新增產品程式碼、不執行 Terraform apply/destroy，也不執行 ASR／TTS 模型效能 benchmark。若下文提到 test、plan、smoke 或 integration，均是 main 已有的驗證證據類型或建議的證據解讀，不表示本階段已重新執行。

### 1.1 設計目標

- 說明 Flutter App、API Gateway、Python Lambda、AgentCore Runtime、AWS data plane 與 Terraform 如何組成單一產品。
- 明確分離 realtime tool-calling ownership、session close、batch materialization、summary projection 與 caregiver read API。
- 以 `docs/api.md` 作為唯一前後端契約，並以 `docs/framework.md` 作為資料模型與寫入規則來源。
- 保留 remote-only ASR/TTS、同語言 route、production approval gate、typed failure 與 PII 邊界，但不把模型品質或效能納入本 consolidated spec。
- 讓每項能力可以從指定 main commit 的 App、backend、Terraform、data、docs 與 tests 路徑追溯。

### 1.2 非目標與本階段不執行內容

- 不新增、重構或修改 Flutter、Python、Terraform、data 或文件產品內容。
- 不執行 implementation tasks，也不建立 `tasks.md`。
- 不把 `eval/`、未合併 branch、未合併 agent patch 或工作樹新增檔案視為 main 完成證據。
- 不訓練、部署、benchmark 或比較 ASR/TTS 模型；不以 P95、WER、MOS 或其他性能數字宣稱語音完成。
- 不把 AWS provider 的真實可用性、Bedrock 生成品質、S3/SQS exactly-once 或 extraction accuracy 推定為本文件可由 source 靜態證明的事項。
- 不建立 DynamoDB `memories` 表；長期記憶由 AgentCore 託管服務管理。

## 2. 已完成狀態的整體架構

### 2.1 邊界與資料流

產品由六個邏輯邊界組成：

1. **Flutter App**：單一行動 App，登入、同意、角色分流與薄 client UI 在 `app/`；長者模式負責免手持輸入／播放與行程互動，照護者模式負責長者、摘要、事件、統計與 routine 管理。App 不決定 provider、不產生 server-owned ID、不承擔智慧分類。
2. **API ingress**：Cognito JWT 驗證後由 API Gateway `/v1` 將請求路由至 Lambda。所有公開 request/response、錯誤、ID、時間、分頁與 enum 以 `docs/api.md` 為準。
3. **Realtime Lambda path**：`chat.py` 處理 text/audio、Turn idempotency、session routing、ASR、AgentCore invocation、TTS 與 atomic terminal commit；資料 handlers 處理 elders、caregivers、routines、events、summaries、stats。
4. **AgentCore Runtime**：`backend/src/agentcore_runtime/` 執行 LangGraph 對話大腦、託管長期記憶、衛教 RAG 與 tool calling。它不與一般 Lambda 共用部署包；tool calling 透過 tools Lambda 寫入 routine mutation、canonical completion event 與 safety event。
5. **Offline materialization**：session closer 將 closed session 凍結為 immutable ordered snapshot 後送 SQS；batch extractor 依 static chunk manifest、taxonomy、concept retrieval、canonical key 與 in-memory dedup 產生一般生活事件，DLQ reconciler 負責安全化失敗收斂與告警。
6. **AWS persistence/IaC**：DynamoDB 保存 elders、conversations、events、daily_summaries、routines；S3 保存 TTS 音檔與衛教文件；SQS/EventBridge/CloudWatch/SNS 支援 batch、summary、sweep、metrics 與 alerting；Terraform 定義資源、IAM 與 provider gate。

簡化資料流如下：

```text
Flutter
  ├─ Cognito login/consent/role routing
  └─ HTTPS /v1
       │
       ▼
API Gateway + JWT + throttling
       │
       ├─ chat Lambda
       │    ├─ audio canonicalization → remote ASR
       │    ├─ AgentCore Runtime → Bedrock/RAG/memory
       │    │                         └→ tools Lambda → routines/events/notify
       │    ├─ remote TTS → S3 object key → presigned URL
       │    └─ DynamoDB turn/session atomic commit
       │
       ├─ session closer → freeze → SQS
       │                         └→ batch extractor → events/DynamoDB
       │                              └→ DLQ reconciler → failed/alert/replay
       │
       └─ data APIs → DynamoDB
              └─ summary generator / EventBridge → daily_summaries
```

### 2.2 Realtime 與 batch 的 ownership

| 能力 | 唯一主要 owner | 完成狀態中的寫入責任 |
|---|---|---|
| Chat reply、ASR/TTS orchestration | Chat Lambda + remote providers | 產生一個 Turn terminal result |
| Routine create/update/deactivate/complete | AgentCore tool calling（對話）或 routine API（照護者） | 建立不可變 routine version；complete 只建立 canonical completion event |
| 潛在高風險 safety signal | AgentCore `notify_caregiver` tool | 先建立可冪等 safety event 並通知照護者 |
| 一般生活事件 | Batch extractor | close 後從 frozen snapshot 建立 normal events；不改 routine |
| Safety enrichment | Batch extractor | 以既有 safety canonical identity 增加 revision/evidence，不新增事件 |
| Session state/snapshot | Session closer | `active → closing → closed`；closed 後 input 不可變 |
| Summary | Summary generator | 讀取 events、turn、routine occurrence 與 batch state，寫入衍生快照 |
| Long-term memory | AgentCore managed service | 不寫入 DynamoDB memories table |

這個 ownership 分界防止 realtime 與 batch 同時把同一件事當成不同資料來源：routine occurrence 的真理是 completion event，normal event 的真理是 canonical event，summary 只是帶 `data_status` 的衍生快照。

## 3. 元件設計

### 3.1 Flutter App

主要模組與責任如下：

- `app/lib/app_router.dart`、shared auth/consent screens：讀取本地 consent 與 Cognito 身分，依 JWT 是否含 `elder_id` 分流長者／照護者；登出時清除 auth、session 與受保護 route。
- `app/lib/elder/`：今日行程、放大行事曆、免手持 Chat、語音錄製／播放、照護者綁定。
- `app/lib/caregiver/`：長者列表、設定、摘要、timeline、stats、routine 管理。
- `app/lib/shared/services/`：`api_client`、`api_repository`、`chat_session`、`session_store`、audio、notification 與 routine sync；負責呼叫契約，不把智慧分類、canonical identity 或 provider 選擇搬到裝置。
- `app/lib/shared/models/`：API model 與 nullable `reply_audio_url` 邊界；錯誤分支依穩定 `error.code`，不依賴可調整的 message。

App 送出新的 Chat input 時產生新的 `client_request_id`；網路重送沿用同一 ID。停止免手持、離開對話或切換長者時呼叫 close endpoint；close 收到 `REQUEST_IN_PROGRESS` 時重試同一 close call，不產生新的 request ID。

### 3.2 API Gateway、認證與共通回應

API Gateway 提供 `/v1` 路徑、Cognito JWT authorizer、節流與 Lambda integration。handler 只從 token 取得 actor identity：長者 token 的 `elder_id` 只能存取自己，照護者只能存取 `caregiver_ids` 已包含其 Cognito `sub` 的長者。對外照護者識別由 `sub` 穩定衍生為 `cg_`，不回傳 Cognito `sub`。

共通回應由 `src.shared.responses` 建立：

```json
{ "error": { "code": "REQUEST_IN_PROGRESS", "message": "請稍後再試" } }
```

主要錯誤語意為：401 `UNAUTHORIZED`、403 `FORBIDDEN`、資源防洩漏用 404、409 `REQUEST_IN_PROGRESS`／`IDEMPOTENCY_CONFLICT`、400 `INVALID_PARAMETER`／`AUDIO_TOO_LONG`／`ROUTINE_NOT_SCHEDULED`、429 `THROTTLED`、500 `INTERNAL_ERROR`。端點細節、公開欄位與分頁 token 一律回到 `docs/api.md`，不在各 handler 另立契約。

### 3.3 Chat、ASR、AgentCore 與 TTS

`POST /v1/chat` 的 realtime 順序固定如下：

1. 驗證 actor、elder ownership、`lang` 與 text/audio 擇一。
2. 正規化 request 並計算 `request_hash`。
3. 先以 elder scope + `client_request_id` 查既有 Turn，再做任何 session selection/reserve。
4. 新 Turn 才選擇或建立 active session，並以 transaction reserve processing Turn 與 inflight slot。
5. audio 由 ASR facade 轉 canonical PCM（mono、16 kHz、S16LE、單句最多 60 秒）；final transcript 非空才進入與 text 相同的對話 path。
6. 將明確 `lang`、必要 context 與 session/turn correlation 交給 AgentCore Runtime；routine/safety tool side effects 在 Chat 回應前完成。
7. 以相同語言及 profile 腔調執行 TTS；成功音檔寫 S3、DynamoDB 僅存 object key，回應時簽發短效 presigned URL。
8. 以單一 terminal transaction 提交 completed response、routine/event mutations、移除 inflight、turn ordering 與 counters；TTS 全部失敗時仍提交文字 Turn，`reply_audio_url=null`。
9. HTTP delivery 失敗不得將已成功 business commit 的 Turn 改成 failed；相同 request ID 只 replay terminal 結果。

ASR 與 TTS 都是 remote-only：Lambda 不下載、載入或執行模型。

- `lang=zh-TW`：ASR 使用 Amazon Transcribe Streaming `zh-TW` 主路由；可用同語言、已核准的 CE remote fallback。
- `lang=hak`：只從 elder profile 取得六腔 `hakka_dialect`，reserve 時保存 snapshot；Formo 對應腔調為主路由、CE 為同語言備援。不得用中文 route 偽裝客語能力。
- TTS 依 `lang` 與六腔 snapshot 選 route；客語失敗不切中文 voice。自託管 provider 的 enable、license/access/capacity/runtime/approval gate 任一未完成時 fail closed；managed provider 的既有路徑仍可用。
- telemetry 只輸出 allowlist 的類別、狀態、latency/計數等分類資訊，不輸出音訊、transcript、reply text、token、PII、endpoint 或原始 provider response。

### 3.4 AgentCore Runtime 與 tools Lambda

AgentCore Runtime 是唯一不在一般 Lambda 執行的對話大腦，負責 LangGraph orchestration、對話模型、AgentCore managed memory 與 Bedrock Knowledge Base retrieval。它不直接取得 API actor 權限；需要 side effect 時呼叫 tools Lambda，tools Lambda 再以 authenticated elder scope 與固定 canonical action key 寫入資料。

工具 ownership：

- `create_routine`／`update_routine`／`deactivate_routine`：寫入新的不可變 routine version。
- `complete_routine`：依 `elder_id + routine_id + routine_date` 建立 canonical completion event，不在 routines table 保存 done 狀態。
- `notify_caregiver`：首次 safety episode 建立 `SAFETY#alert_id` event 並觸發通知；後續 escalation/mitigation 使用相同 alert identity。
- `update_elder_profile`：透過受控寫入更新 profile 或以來源為 `agent` 新增 health note。

Chat 只有在 tool side effect 成功提交後才將 `routines_updated=true` 或 safety 成功狀態反映在 terminal response。衛教回答可從 Knowledge Base 取得可追溯內容，但產品邊界是一般生活／健康資訊，不提供醫療診斷。

### 3.5 Session、Turn 與 close

Turn 是 request idempotency 與 business commit 單位；Session 是 immutable snapshot 與 batch ownership 單位。

- Turn `conversation_id=cnv_...` 由 elder scope 與 client request identity 穩定產生；request 狀態為 processing/completed/failed，並帶 request lease。
- Session `session_id=ses_...` 只允許 `active → closing → closed`；active 保存有序 `turn_ids` 與 inflight IDs，並受 turn、inflight、input bytes 上限控制。
- 同 scope/id/hash 的 completed/failed Turn 直接 replay；不同 hash 回 409；有效 processing lease 回 409；lease 過期才可條件式接管原 reservation。
- close 只在沒有 inflight 且 snapshot 驗證完整時 freeze ordered turns、input bytes 與 snapshot hash，進入 closed 並設 `batch_status=pending`。closed 不接受新 Turn、不重排或改寫 frozen input。
- close 與 reserve 競爭由 conditional transition 決定：reserve 先成功則 close 等待；close 先進 closing 則新 request 必須使用新的 active Session。
- closed persistence 與 SQS SendMessage 非原子；若中間失敗，`BATCH#PENDING` recovery sweep 重投。consumer 依 session ID + snapshot hash + lease + conditional writes 達成可恢復的 at-least-once semantics，不假設 exactly-once。

### 3.6 Batch extractor、SQS、DLQ 與 static chunks

Session closed 後，batch planner 對 immutable ordered input 建立 compact `chunk_manifest`。manifest 保存 chunk ID、ordinal、core range 與必要 context range，不複製逐字稿、不建立公開 chunks resource。每個 turn 恰好落在一個 core range；context overlap 只供理解，context-only turn 不可 emit event。

manifest 第一次成功保存使用 attribute-not-exists 條件；retry、duplicate delivery、DLQ replay 與人工 replay 都重用既有 manifest。chunk ID 由 snapshot hash、first/last core turn ID 與 ordinal 的 stable hash 產生，與模型 topic label 或 chunk delivery 無關。

worker lifecycle：

- `pending → processing` 需 lease；processing lease 過期才可 recovery claim。
- retryable error 由 worker throw 交給 SQS retry/redrive，不先把 session 標成 failed。
- permanent validation/conflict 可條件式寫 `failed`、清 lease、保存安全化 error。
- DLQ reconciler 只有在 session snapshot hash 相符且尚未 terminal 時才收斂 failed、清 lease、發安全化告警；hash 不符不得誤改 session。
- 人工 replay 先條件式做 `failed → pending`，再從 frozen state 與既有 manifest 重建工作；正常 worker 不自行 reopen failed。

事件 extraction 先以 configurable taxonomy 與 Bedrock embedding/S3 Vectors concept retrieval 取得候選，再依台灣日界、`EVENT_SLOT_MINUTES`、Subject、Predicate 產生 canonical key。Session 內同時間槽且同 Subject/Predicate 的資訊先在記憶體合併，保留最完整 detail 並聯集 evidence conversation IDs。疑似 routine completion 只在 normal event 的 `structured_detail.suspected_routine_id` 標記，不寫 completion event。

### 3.7 Summary、events、stats 與 caregiver read model

Summary generator 同時支援 EventBridge schedule 與 `POST /summaries/generate`。它讀取 turn、events、routine version/history、completion events 與相關 session batch state，依台灣日界與 `input_through_at` 產生固定七類 sections：`diet`、`activity`、`sleep`、`medication`、`wellbeing`、`safety`、`other`。

- active/closing，或 closed 但 batch pending/processing/failed 的相關 session，計入 `pending_session_count`，摘要為 `partial`。
- 只有 pending count 為零且所有相關 closed batch completed 才能為 `complete`。
- 同一日期摘要以較新 cutoff、complete 優先、同完整度較新 generated time 的順序條件式收斂。
- routines.items 每個 `routine_id + date` 最多一項；completion event 優先決定 done 與完成時版本，否則依 cutoff 前有效版本與 grace period 得到 pending/missed。

`GET /events` 以 `events-by-time` query、台灣日期邊界、固定時間鍵與不透明 next token 提供穩定時間軸；只回公開欄位。`GET /stats` 即時計算已完成 Chat Turn、活動天數、routine occurrence 與 canonical completion event，不把 summary 當作統計真理。照護者 App 將這些 read model 組成長者列表、摘要、timeline、stats 與 routine UI；長者 App 組成今日行程、calendar、Chat 與 caregiver link UI。

## 4. 資料模型與一致性

### 4.1 DynamoDB tables

| Table | Key / index | 角色 |
|---|---|---|
| `elders` | PK `elder_id` | persona、語言／六腔、health notes、family、caregiver binding |
| `conversations` | PK `elder_id`, SK `record_id`; time/session/state GSIs | Turn、Session metadata、frozen snapshot、chunk manifest |
| `events` | PK `elder_id`, SK `event_id`; `events-by-time` GSI | normal、routine completion、safety canonical events |
| `daily_summaries` | PK `elder_id`, SK `date` | 帶 `data_status` 的每日衍生快照 |
| `routines` | PK `routine_id`, SK `version`; current/history GSIs | 不可變 routine definitions/versions |

Long-term memory 不在上述 tables 中，使用 AgentCore managed service。

### 4.2 重要 identity 與 write rules

- `elder_id`、`routine_id`、`event_id`、`conversation_id`、`session_id`、`cg_id`、`note_id` 都由後端依契約產生或穩定衍生；client 不能指定 server-owned identity。
- routine completion canonical identity 固定為 `elder_id + routine_id + routine_date`；同日不同 routine version、對話完成與手動完成收斂同一 event。
- safety identity 固定由 `SAFETY#alert_id` 決定；合法 enrichment 只遞增 revision、合併 evidence/detail/confidence，不改 event identity。
- normal event identity 固定由台灣日期、slot、normalized Subject、normalized Predicate 決定；不使用 chunk、track、模型版本或自然語言 detail。
- `created_at`、server-owned binding、ID 與 routine history 不可由公開 patch 覆寫；成功 mutation 才更新 `updated_at`。
- `events`、`routines`、`daily_summaries` 不保存公開音訊 URL；TTS 只在 S3 保存音訊，DynamoDB 保存 object key，API 動態簽發 15 分鐘 presigned URL。
- event API 不暴露 `canonical_event_key`、`extraction_track`、`concept_id`、`taxonomy_version`、chunk、revision、evidence internals；summary API 只暴露契約允許的 `data_status` 與 pending count。

## 5. 基礎設施與設定邊界

Terraform 由 `terraform/providers.tf`、`versions.tf`、`api_gateway.tf`、`cognito.tf`、`lambda.tf`、`dynamodb.tf`、`s3.tf`、`sqs.tf`、`eventbridge.tf`、`cloudwatch.tf`、`agentcore.tf`、`bedrock_kb.tf`、`s3_vectors.tf`、ASR/TTS config/model files 與 `variables.tf` 組成。

資源邊界包括：

- API Gateway + Cognito JWT、Lambda execution roles 與最小 IAM。
- DynamoDB tables/indexes、S3 TTS/knowledge objects、SQS queue/DLQ、EventBridge schedules、CloudWatch metrics/alarms、SNS alert topic。
- Bedrock Knowledge Base、S3 Vectors concept index、AgentCore Runtime 與 managed memory integration。
- `ASR_CONFIG_JSON`、`TTS_CONFIG_JSON` 是唯一語音 route 設定來源；包含 route、provider、language/dialect、enable/approval/capability 組合。
- `asr_enable_endpoints=false` 或 TTS endpoint 未啟用時，不建立自託管 GPU endpoint；managed provider path 保留。未通過 license、access、quota/capacity、runtime 或 approval 的 provider 不得被 runtime 當成 fallback。
- batch/summary/session sweep 透過 SQS retry/DLQ、EventBridge schedule、lease env、CloudWatch metrics 與 SNS 告警互相對應。
- 交付格式固定為 Terraform `.tf` 與 Terraform `.terraform.lock.hcl`。本機若使用 OpenTofu 只作驗證，不能把 registry/hash 改寫成 OpenTofu 交付物；本階段不執行 apply/destroy。

## 6. PII、安全與可觀測性

- 首次 App 啟動顯示同意與資料保留政策；未同意前不進受保護功能。
- 所有 API 以 Cognito JWT 做認證授權；傳輸 HTTPS；DynamoDB/S3 靜態加密；依 retention policy 管理保存與刪除。
- demo、seed、test 與競賽 AWS 帳號只使用 synthetic persona、合成音訊、非真實健康內容；不得匯入真實長者聲音、逐字稿、個資或健康資料。
- audio 只在 Lambda memory 內處理；ASR 不使用 batch transcription 或 S3 暫存；TTS 合成文字／音訊不進 log，不蒐集聲紋。
- allowlist telemetry 可觀測 chat latency、structured output failure、safety rule hit、session turn/input bytes、chunk count、batch attempts、SQS duplicate/DLQ、partial ratio、summary backfill latency、dedup merge rate、type/concept distribution 與 embedding/retrieval latency；不記錄原始內容。
- 不由 telemetry 推導模型性能保證；本 consolidated spec 的語音完成證據限於 remote route、語言／腔調一致、approval gate、typed error、fallback 與 PII redaction。

## 7. 錯誤、恢復與一致性策略

| 失敗情境 | 系統行為 |
|---|---|
| 無效／缺少 Cognito token | Gateway 回 401 共通錯誤 |
| resource ownership 不符 | 一般資料 API 回 403；防洩漏 endpoint 依契約回 404 |
| 同 request ID 不同 payload | 回 409 `IDEMPOTENCY_CONFLICT`，不產生副作用 |
| 相同 Turn lease 尚有效 | 回 409 `REQUEST_IN_PROGRESS`；lease 到期才可接管 |
| close 有 inflight 或 snapshot 不完整 | 回 409／保留可恢復狀態，不進 closed |
| ASR 設定／音訊／provider 不可用 | typed error 依 route 規則處理；不可把不可 fallback 錯誤誤吞 |
| TTS 全部失敗 | 文字 Turn 仍完成，audio URL 為 null，不回寫 failed |
| retryable batch error | throw 交 SQS retry/redrive，保留可恢復 batch state |
| permanent batch error | 條件式 `failed`、清 lease、保存安全化錯誤 |
| DLQ snapshot hash 不符 | 不改 session，保留訊息並告警 |
| routine 未排程日期 | 400 `ROUTINE_NOT_SCHEDULED`，routine/event 不變 |
| event canonical conflict | 相同資料冪等回既有結果；互斥資料保留既有事實並記 conflict |
| summary 來源未完整 | `partial` 與精確 pending count；後續 batch 完成後可條件式升級 `complete` |

所有資料層狀態推進使用 conditional write、transaction、strong read 或 lease，避免把 GSI 最終一致讀誤當成 freeze、ownership 或成功寫入的真理。

## 8. Main 完成證據與驗證解讀

### 8.1 證據索引

| 能力 | main 證據 |
|---|---|
| Flutter auth/role/elder/caregiver UI | `app/lib/app_router.dart`、`app/lib/elder/`、`app/lib/caregiver/`、`app/lib/shared/`、`app/test/` |
| Chat/session/turn | `backend/src/handlers/chat.py`、`backend/src/shared/sessions.py`、`turns.py`、`session_closer.py`、`app/lib/shared/services/chat_session.dart`、`session_store.dart`、`backend/tests/test_chat.py`、`app/test/chat_session_test.dart` |
| AgentCore/tools/routine/safety | `backend/src/agentcore_runtime/`、`backend/src/handlers/tools.py`、`backend/src/shared/routines.py`、`backend/tests/test_module_b_end_to_end.py`、`app/test/routine_sync_test.dart` |
| ASR/TTS remote-only | `backend/src/shared/asr/`、`backend/src/shared/tts/`、`backend/tests/asr/`、`backend/tests/tts/`、`docs/asr/`、`docs/tts/` |
| Batch/extraction/recovery | `backend/src/handlers/batch_extractor.py`、`dlq_reconciler.py`、`backend/src/extraction/`、`backend/tests/test_batch_extractor.py`、`test_extraction_*.py` |
| Events/summaries/stats | `backend/src/handlers/events.py`、`summaries.py`、`stats.py`、`summary_generator.py`、`backend/tests/test_events_handler.py`、`test_module_b_end_to_end.py` |
| Persistence/API contract | `backend/src/shared/db.py`、`models.py`、`responses.py`、`docs/framework.md`、`docs/api.md`、`app/lib/shared/models/`、`api_client.dart`、`api_repository.dart` |
| Terraform/AWS boundary | `terraform/*.tf`，特別是 API Gateway、Cognito、Lambda、DynamoDB、S3、SQS、EventBridge、CloudWatch、AgentCore、Bedrock KB、S3 Vectors、ASR/TTS config/model files 與 lock file |
| PII/synthetic data/docs | `docs/pii.md`、`data/personas/`、`data/scenarios/`、`data/knowledge/`、`docs/user-journey.md`、`docs/deliverables/user-journey.md`、`docs/adr/` |

### 8.2 驗證類型界線

- **可寫成 correctness property 的 domain logic**：request hash/idempotency、provider gate decision、canonical identity、session state machine、dedup、manifest reuse、summary classifier/winner、event projection、stats reducer、API allowlist serializer。這些 property 以 mock/in-memory model 驗證，不代表對 AWS 執行 100 次。
- **Example/edge**：同意與角色路由、登出、App close retry、未排程 routine、特定錯誤分支與畫面 presence。
- **Integration**：API Gateway/Cognito、DynamoDB conditional/transaction/strong read、S3 presign、AgentCore/Knowledge Base、SQS/DLQ/EventBridge/CloudWatch/SNS 與 provider adapter。
- **Smoke/contract audit**：Flutter screen inventory、Terraform resource/lock、文件 cross-link、synthetic data policy、remote-only 與不做模型性能測試的範圍檢查。

以上分類是對 main 完成證據的正確解讀；本 design 不把尚未在指定 commit 中證明的部署結果、第三方服務品質或語音模型性能寫成既成事實。

## 9. Correctness Properties

以下 properties 是 domain-level 可執行的正確性陳述。每個 property 應以至少 100 次隨機迭代驗證；測試應以註解標示 `Feature: main-completed-product-state, Property N: ...`。AWS resource wiring、UI rendering、文件存在性與 main provenance 另以 example/integration/smoke evidence 驗證，不強行轉成 property。

### Property 1：Server-owned 長者資料不變量

**For any** 合法建立 request 與公開欄位 patch，後端產生的 `eld_` ID、creator binding、created_at、預設 health_notes/family 與 server-owned 欄位不得由 client 覆寫；成功公開欄位變更才更新 updated_at，失敗操作不得改變原資料。

**Validates: Requirements 2.1, 2.5**

### Property 2：照護者綁定冪等且保留首次時間

**For any** 合法長者與照護者 ID，重複執行 binding 應最多保留一個 `cg_` binding，第一次 `linked_at` 保持不變，後續相同 binding 回傳既有結果而不產生第二筆關係。

**Validates: Requirements 2.3**

### Property 3：Audio canonicalization 與同一路徑

**For any** 可解碼且不超過 60 秒的 audio input，ASR facade 輸出必為 mono、16 kHz、PCM canonical audio，並將非空 final transcript 交給與 text input 相同的 realtime orchestration；超過上限的 input 不得進 provider。

**Validates: Requirements 3.2**

### Property 4：語言與腔調 snapshot 穩定

**For any** 六腔 elder profile 與 `lang=hak` 的新 Turn，reserve 保存的腔調必等於 reserve 當下 profile 值，request body 不得覆寫它；reserve 後 profile 改變不得改變該 Turn 的 ASR/TTS route。

**Validates: Requirements 3.4**

### Property 5：語音 provider gate 與同語言 fail-closed

**For all** provider route 設定與 approval evidence 組合，只有 route enabled、能力可用且所有必要 license/access/capacity/runtime/approval evidence 齊全時才可 invocation；缺任一 gate 時自託管 provider 必須 disabled/fail-closed，fallback 只能選同語言的受控 managed 或已核准 route，不得跨語言或使用未核准模型。

**Validates: Requirements 3.3, 3.7, 9.2, 9.3, 9.4**

### Property 6：TTS 失敗不回滾文字 business commit

**For any** 合法 Chat reply 與任意 TTS provider 成功／失敗／逾時序列，若對話與 routine/event business commit 成功，Turn 必為 completed；TTS 全敗時 response 的 `reply_audio_url` 必為 null，且不得因音訊失敗重複執行 business side effects。

**Validates: Requirements 3.5, 3.6**

### Property 7：Telemetry 與公開 response 的敏感欄位封閉性

**For any** 含音訊、transcript、reply text、token、PII、endpoint、raw provider response 或 extraction internals 的內部資料，telemetry serializer 與公開 response serializer 只能輸出各自 allowlist 欄位，不得洩漏禁止欄位。

**Validates: Requirements 3.8, 8.6**

### Property 8：Turn routing 先查冪等再 reserve

**For any** elder scope、client request ID、request hash、existing Turn 狀態與 Session 狀態組合，Chat API 必先完成 Turn lookup/hash 判定；只有查無 existing Turn 的新 ID 才能建立／選擇 Session 或 reserve inflight，既有 processing Turn 不得再次 reserve。

**Validates: Requirements 4.1, 4.3, 4.4**

### Property 9：Terminal Turn replay 冪等

**For any** 相同 elder scope、client request ID 與 request hash 的 completed 或 failed Turn，重送任意次都應回傳同一 terminal result、conversation/session identity 與穩定錯誤（failed），且 business side effects 數量不增加。

**Validates: Requirements 4.2**

### Property 10：Session close 的 immutable state machine

**For any** 沒有 inflight 且 snapshot 驗證完整的 active Session，close 應依 `active→closing→closed` 凍結有序 turn IDs、counts、input bytes 與 snapshot hash，設 batch pending；**for any** 有 inflight 或驗證不完整的 Session，close 不得進 closed，必須保留可恢復狀態。

**Validates: Requirements 4.5, 4.6**

### Property 11：Closed Session replay 與新 request 分流

**For any** closed Session，既有 completed Turn 的相同 request ID 必須 replay 原 session/result；全新的 client request ID 不得追加 closed Session，必須被導向新的 active Session。

**Validates: Requirements 4.7**

### Property 12：Safety event cross-track enrichment

**For any** 已由 realtime 建立的 safety event 與後續 batch evidence，enrichment 應保留 event ID/canonical identity 與既有事實欄位，僅以條件式 revision 更新 detail、confidence 與 evidence union，不得建立第二筆 safety event。

**Validates: Requirements 5.4**

### Property 13：Routine occurrence canonical identity

**For any** elder、routine、routine version、routine date 與 completion source 組合，同一 logical occurrence 最多一個 canonical completion event；對話與手動完成收斂到同一 event，存在 event 即為 done，沒有 event 才依 schedule、cutoff 與 grace period 判定 pending/missed。

**Validates: Requirements 5.6**

### Property 14：Batch event canonicalization 與台灣日界

**For any** 合法 extraction input、時區表示、taxonomy version、Subject/Predicate alias 與 slot 設定，canonical event 應使用台灣日期、固定 slot 與 normalized Subject/Predicate，identity 不得依賴 chunk、track、模型版本或 detail 文字，且寫入 taxonomy version。

**Validates: Requirements 6.2**

### Property 15：Frozen snapshot 內記憶體去重

**For any** immutable ordered snapshot 中的事件集合，同一時間槽內相同 normalized Subject 與 Predicate 應合併為至多一筆，保留最完整 detail 並聯集 evidence conversation IDs；不同 slot 或不同 Subject/Predicate 不得被錯誤合併，context-only turn 不得 emit。

**Validates: Requirements 6.3**

### Property 16：Chunk manifest retry reuse

**For any** frozen session snapshot 與任意 retry、duplicate delivery、DLQ replay 或 manual replay 序列，首次成功保存的 manifest、core ranges、ordinal 與 chunk IDs 必須保持完全相同；後續執行不得重新規劃或重新分配 core turns。

**Validates: Requirements 6.4, 6.8**

### Property 17：Batch 不擁有 routine completion

**For any** batch extraction 判定為疑似 routine completion 的輸入，結果只能寫一般事件並帶 `structured_detail.suspected_routine_id`，不得建立 canonical completion event、修改 routine version 或改寫 occurrence 狀態。

**Validates: Requirements 6.5**

### Property 18：Canonical event conditional write

**For any** canonical event write 的 retry、完全相同 duplicate、合法 safety enrichment 或互斥 payload，系統應分別回既有結果、保持 identity、依 revision enrichment，或保留既有事實並記錄 conflict；不得因 retry 產生第二筆 event 或靜默覆寫互斥欄位。

**Validates: Requirements 6.9**

### Property 19：Summary data-status 完整分類

**For any** 相關 Session 狀態集合，若任一 session 為 active/closing，或 closed batch 為 pending/processing/failed，摘要必為 `partial` 且 pending count 等於符合條件的 session 數；只有 pending count 為零且所有相關 closed batches completed 時才為 `complete`。

**Validates: Requirements 7.1, 7.2, 7.3**

### Property 20：Summary winner 收斂規則

**For any** 同一 elder/date 的摘要候選集合，勝者必依 `input_through_at` 較新、同 cutoff 時 complete 優先、同完整度時 generated_at 較新的順序選取；較舊 cutoff 或較低完整度不得覆寫勝者。

**Validates: Requirements 7.4**

### Property 21：Event timeline projection 與穩定分頁

**For any** 合法 event 集合、台灣日期範圍、type filter 與 page size，查詢結果應按固定 `event_time_key` 穩定排序／分頁，只輸出 API allowlist 欄位；canonical key、track、chunk、revision、evidence 與其他 backend internals 不得出現在 response。

**Validates: Requirements 7.5, 8.6**

### Property 22：Stats 只計 canonical completed facts

**For any** 含 completed/failed Turn、跨日互動與 routine occurrence 的資料集合，stats 應只計 completed Chat Turns，正確計算 active days、daily zero-fill、occurrence total 與 canonical completion count，不因重複 completion event 增加計數。

**Validates: Requirements 7.6**

### Property 23：Terraform config serialization 唯一來源

**For any** 合法 ASR/TTS route、provider、language/dialect、approval 與 capability matrix，序列化再解析 `ASR_CONFIG_JSON`／`TTS_CONFIG_JSON` 應保留相同設定；runtime 不得需要另一組散落 provider 設定才能選路由。

**Validates: Requirements 9.3**

### Property 24：Summary／event／API model 的公開欄位封閉

**For any** backend entity 含內部 identity、revision、chunk、taxonomy、evidence、Cognito sub 或 storage key 的資料，API serializer 應只回傳 `docs/api.md` 允許的欄位、enum、時間格式與 opaque pagination token；內部欄位不得因資料內容變化而外洩。

**Validates: Requirements 8.1, 8.6**

## 10. Requirements traceability 與證據限制

- Requirements 1 的 consent、role、logout、401/403/404 主要以 Flutter widget/navigation、Cognito/API integration 與 `auth` 測試證據表示，不把有限 UI 狀態誤寫成 PBT。
- Requirements 2–7 的 data transformation、state machine、canonical identity、summary/stats reducer 由 Properties 1–22 覆蓋；AWS transaction、SQS、AgentCore、S3、DynamoDB strong read 則由 main 的 unit/integration/end-to-end 測試與資源 contract 證據覆蓋。
- Requirement 8 的 encryption、retention、synthetic data 與 memory boundary 是 PII/IaC/data audit；serializer allowlist 由 Properties 7、21、24 覆蓋。
- Requirement 9 的 Terraform resource inventory、IAM、flag、lock file 與 OpenTofu 不改寫是 smoke/contract/provenance evidence；Properties 5、23 只描述 provider gate/config 的 domain invariant。
- Requirement 10 的文件與 main commit traceability 是 docs/link/source audit；不在本階段重新執行，也不以未合併內容補強證據。

本設計至此完成 design phase；下一階段若要產生 tasks，應由 orchestrator 另行啟動，不能在本階段推進 implementation。
