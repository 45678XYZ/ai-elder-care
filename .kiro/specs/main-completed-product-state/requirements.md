# Requirements Document

## Introduction

本文件是「智慧長照陪伴 App」在 Git `main` 分支已完成產品狀態的 consolidated spec。內容不是新的實作 backlog，而是依 `main` 的完成證據反向整理產品目前應具備的行為、資料契約、部署邊界與使用者旅程。

本文件的基準是 `main` commit `7fce81c7e8669ba805541a98fbc0646db9328ac7`。目前工作樹中其他分支或未合併 agent 變更不屬於本文件的證據；本文件也不以單一 `app/lib/main.dart` 代表產品狀態。

本文件不要求設計、執行或實作 ASR／TTS 模型效能測試，也不要求新增產品程式碼。ASR／TTS 在本文件中只描述 main 已存在的遠端路由、核准閘門、語言一致性與失敗語意。

## Glossary

- **智慧長照系統**：由 Flutter App、Python Lambda、AgentCore Runtime、AWS 服務與 Terraform IaC 組成的產品整體。
- **Flutter App**：`app/` 下的單一行動 App；登入後依使用者角色呈現長者模式或照護者模式。
- **長者模式**：提供今日行程、免手持語音對話、語音回覆、行程完成與照護者綁定的 App 介面。
- **照護者模式**：提供長者管理、每日摘要、事件時間軸、統計與例行公事管理的 App 介面。
- **Cognito 身分**：AWS Cognito JWT token 所代表的已登入使用者；token 的 `elder_id` claim 表示長者角色，沒有該 claim 表示照護者角色。
- **API Gateway**：提供 `/v1` API 路徑、JWT 驗證、節流與 Lambda 整合的 AWS 入口。
- **資料 API**：由 Python Lambda 提供的 elders、caregivers、events、routines、summaries 與 stats API。
- **Chat API**：`POST /v1/chat` realtime 對話端點，以及 `/v1/chat/sessions/{session_id}/close` session 關閉端點。
- **Turn**：一次長者輸入與 AI 回覆的對話輪次；每個 Turn 具有 `client_request_id`、穩定的 `conversation_id` 與 terminal 狀態。
- **Session**：一組可接納 Turn 的對話範圍；Session 依序經過 `active`、`closing`、`closed`，closed 後輸入快照不可變。
- **Realtime path**：Chat API 在回應前執行的 ASR、對話、Agent tool calling、routine 變更、潛在安全事件與 TTS 流程。
- **Batch pipeline**：Session closed 後執行的 snapshot、`direct_seven` 事件萃取、canonical 收斂、去重、SQS retry／DLQ recovery 與摘要補齊流程。
- **Direct Seven Pipeline**：唯一的萃取 pipeline（`direct_seven`）。不分塊、不做概念檢索、不做前置分類；對 frozen turns 依 `SEVEN_BATCH_CHAR_LIMIT` 在 turn 邊界貪婪分批，每批一次 LLM 呼叫直接萃取七大高階類別，再經共用尾段完成時序解析、canonical identity、slot 去重與型別驗證。
- **AgentCore Runtime**：執行 LangGraph 對話大腦、託管長期記憶、衛教 RAG 與 tool calling 的 AWS runtime；不在 Lambda 執行。
- **Routine**：照護者或對話大腦建立的不可變版本化例行公事計畫；完成狀態由 canonical completion event 衍生。
- **Canonical Event**：以穩定 canonical key 產生、可跨 realtime、batch 與 manual 路徑冪等收斂的事件紀錄。
- **Daily Summary**：依台灣日界與指定 cutoff 產生的每日衍生摘要，包含七類 sections、routine 統計與 `data_status`。
- **ASR**：將 Chat API audio 輸入轉為逐字稿的語音辨識子系統；Lambda 只呼叫受控遠端 provider。
- **TTS**：將 AI `reply_text` 轉成回覆音訊的語音合成子系統；Lambda 只呼叫受控遠端 provider，成功音訊存於 S3。
- **Remote-only**：Lambda 不下載、載入或執行 ASR／TTS 模型，只呼叫 Amazon Transcribe、SageMaker real-time endpoint 或 Polly。
- **Production approval gate**：自託管 provider 必須同時具備授權、存取、配額、容量、staging/runtime 證據與正式核准狀態，才可接受 production invocation。
- **主分支完成證據**：位於 main commit `7fce81c` 的 source、測試、Terraform、data 與 docs；證據索引不等同本階段重新執行測試。
- **模擬資料**：`data/` 下的 synthetic persona、情境腳本、seed 內容與衛教文件；不含真實長者個資、聲音、逐字稿或健康資料。
- **台灣日界**：所有日期查詢、routine occurrence、事件 canonical key 與摘要 cutoff 使用 `+08:00`。

## Main 完成證據基準

本 consolidated spec 以以下 main 內容作為已完成產品狀態的可追溯證據：

1. **Flutter App**：`app/lib/elder/`、`app/lib/caregiver/`、`app/lib/shared/`、`app/lib/theme/` 與 `app/lib/app_router.dart` 已形成登入、角色選擇、長者模式、照護者模式、API models、API services、audio、notification、session 與 demo repository 的完整模組；`app/test/` 已包含 auth、first-run、chat session、routine、health note、caregiver link、screen smoke 與語音相關行為測試。
2. **Backend**：`backend/src/handlers/` 已包含 chat、session closer、batch extractor、DLQ reconciler、elders、events、routines、summaries、stats、tools 與 Cognito trigger handlers；`backend/src/agentcore_runtime/`、`backend/src/extraction/`、`backend/src/shared/asr/`、`backend/src/shared/tts/` 與共用 auth／db／models／sessions／turns／responses／metrics 模組已存在。
3. **Backend 測試**：`backend/tests/` 已涵蓋 Chat、DynamoDB data layer、events、routines／extraction、Module B end-to-end、ASR provider／router／remote endpoint／telemetry／Terraform contract 與 TTS Terraform contract／行為測試。
4. **Terraform IaC**：`terraform/` 已包含 API Gateway、Cognito、Lambda、Lambda config parameters（SSM）、DynamoDB、S3、SQS、EventBridge、CloudWatch、Bedrock Knowledge Base、Bedrock IAM、AgentCore Runtime、ASR 與 TTS provider configuration／model resources。
5. **資料與文件**：`data/personas/`、`data/scenarios/`、`data/knowledge/`、`docs/framework.md`、`docs/api.md`、`docs/pii.md`、`docs/user-journey.md`、ASR／TTS framework、model catalog、security、ADR 與 deliverables 文件已描述產品資料流、契約與安全邊界。

上述證據以檔案存在、模組分工與測試覆蓋範圍表示 main 的完成狀態；本階段不把未合併的 `eval/` 或其他工作分支內容納入判定。

## Requirements

### Requirement 1：登入、同意與角色分流

**User Story:** 作為長者或照護者，我想在完成資料使用同意後以正確角色進入 App，以便只看到符合身分的功能。

#### Acceptance Criteria

1. WHEN 使用者首次啟動 Flutter App 且尚未完成資料使用同意，THE Flutter App SHALL 先呈現同意與資料保留政策，並在同意完成後才進入受保護產品功能。
2. WHEN Flutter App 取得有效 Cognito 身分，THE 智慧長照系統 SHALL 依 token 的 `elder_id` claim 將使用者導向長者模式或照護者模式。
3. WHEN 使用者登出，THE Flutter App SHALL 清除本地登入狀態、session 狀態與受保護畫面，並回到登入入口。
4. IF API request 缺少有效 Cognito token，THEN API Gateway SHALL 回傳 `401 UNAUTHORIZED` 的共通錯誤格式。
5. IF Cognito 身分嘗試存取未授權的長者資料，THEN 資料 API SHALL 依 endpoint 契約回傳 `403 FORBIDDEN` 或防洩漏規則指定的 `404` 錯誤。

**Main 完成證據：** `app/lib/shared/screens/consent_policy_screen.dart`、`sign_in_screen.dart`、`role_select_screen.dart`、`app/lib/app_router.dart`、`app/test/first_run_flow_test.dart`、`auth_screens_test.dart`、`auth_service_test.dart`、`backend/src/shared/auth.py`、`backend/tests/test_auth.py`。

### Requirement 2：長者與照護者資料及照護關係

**User Story:** 作為照護者，我想建立長者資料、維護健康註記與綁定關係，以便照護資訊能被授權的 App 模式使用。

#### Acceptance Criteria

1. WHEN 照護者建立長者，THE 資料 API SHALL 由後端產生 `eld_` 長者 ID、時間戳與建立者綁定關係，並以預設值補齊缺省的語言、腔調、健康註記與 family 欄位。
2. WHEN 照護者新增或刪除單筆健康註記，THE 資料 API SHALL 以單筆原子操作保留其他同時寫入的註記，並回傳後端產生的 note ID、來源與建立時間。
3. WHEN 長者本人以照護者 ID 建立綁定，THE 資料 API SHALL 以 `cg_` 對外識別、保留首次 `linked_at`，並對重複綁定回傳冪等結果。
4. WHEN 照護者查詢長者、健康註記或已綁定家人，THE 資料 API SHALL 只回傳 Cognito 身分已授權的資料，並以 API 契約指定的分頁與錯誤格式回應。
5. WHEN 照護者更新長者公開欄位，THE 資料 API SHALL 保留 `created_at`、`caregiver_ids` 與後端擁有的識別欄位，並只在成功變更時更新 `updated_at`。

**Main 完成證據：** `backend/src/handlers/elders.py`、`backend/src/shared/models.py`、`backend/tests/test_elders.py`、`app/lib/caregiver/screens/elders_screen.dart`、`app/lib/caregiver/screens/setup_screen.dart`、`app/lib/elder/screens/link_caregiver_screen.dart`、`app/test/create_elder_test.dart`、`health_notes_test.dart`、`link_caregiver_test.dart`。

### Requirement 3：免手持對話與語音一致性

**User Story:** 作為長者，我想用文字或語音與陪伴系統互動並聽到回覆，以便不必持續操作手機。

#### Acceptance Criteria

1. WHEN 長者以 `text` 或 `audio` 呼叫 Chat API，THE 智慧長照系統 SHALL 依 `lang` 執行中文或客語對話流程，回傳 transcript、reply text、session ID、conversation ID、nullable audio URL 與 `routines_updated`。
2. WHEN Chat API 收到 audio，THE ASR SHALL 將輸入正規化為單聲道 16 kHz PCM、驗證單句不超過 60 秒，並將非空白 final transcript 交給同一 realtime path。
3. WHILE Chat API 執行 ASR，THE ASR SHALL 維持 remote-only，中文使用固定 `zh-TW` Amazon Transcribe Streaming 主路由，客語使用 profile 腔調對應的 Formo 路由與同語言 CE 備援。
4. WHEN `lang=hak`，THE 智慧長照系統 SHALL 只從 elder profile 讀取六腔 `hakka_dialect`，並在 Turn reserve 時保存腔調快照供 ASR 與 TTS 共用。
5. WHILE Chat API 產生語音回覆，THE TTS SHALL 依 `lang` 與腔調選擇同語言遠端 route，並將成功音訊以短效 S3 presigned URL 回傳。
6. IF ASR 或 TTS provider 發生設定、核准、能力、逾期或不可用錯誤，THEN 智慧長照系統 SHALL 依 typed error 與 provider fallback 規則處理；TTS 全部失敗時仍完成文字 Turn 並回傳 `reply_audio_url=null`。
7. IF 自託管 ASR／TTS provider 未通過 Production approval gate，THEN 智慧長照系統 SHALL 將該 provider 保持在 fail-closed 狀態並保留可用的受控 managed provider 行為。
8. WHEN ASR、TTS 或 Chat 產生遙測，THE 智慧長照系統 SHALL 只記錄 allowlist 指標與分類資訊，不記錄音訊、逐字稿、合成文字、token、長者個資、endpoint 或原始 provider 回應。

**Main 完成證據：** `backend/src/handlers/chat.py`、`backend/src/shared/asr/`、`backend/src/shared/tts/`、`backend/tests/asr/`、`backend/tests/tts/`、`docs/asr/framework.md`、`docs/tts/framework.md`、`docs/asr/model-catalog.md`、`docs/tts/model-catalog.md`、`app/lib/shared/services/audio_recorder_service.dart`、`audio_service.dart`、`speech_service.dart`、`chat_screen.dart`。

### Requirement 4：Session 生命週期與 Chat 冪等

**User Story:** 作為長者，我想在網路重送、離開對話或切換對象時保留一致的對話結果，以便不重複建立生活副作用。

#### Acceptance Criteria

1. WHEN Chat API 收到新的 `client_request_id`，THE Chat API SHALL 先以 elder scope 與 request hash 判斷既有 Turn，再執行 Session 選擇與 inflight reserve。
2. WHEN 相同 elder scope、`client_request_id` 與 request hash 的 Turn 已 completed 或 failed，THE Chat API SHALL replay 原 terminal 結果與原 conversation/session ID，並保持業務副作用冪等。
3. IF 相同 scope 的 `client_request_id` 搭配不同 request hash，THEN Chat API SHALL 回傳 `409 IDEMPOTENCY_CONFLICT`。
4. IF 相同 Turn 仍在有效 request lease 中處理，THEN Chat API SHALL 回傳 `409 REQUEST_IN_PROGRESS` 並讓 Flutter App 使用同一 request ID 重試。
5. WHEN Session 沒有 inflight Turn 且收到 close request，THE Session closer SHALL 依 `active→closing→closed` 凍結有序 Turn、snapshot hash、input bytes 與 batch pending 狀態。
6. IF Session 仍有未完成 inflight Turn或 snapshot 驗證不完整，THEN Session closer SHALL 回傳或保留 `REQUEST_IN_PROGRESS` 狀態，並讓 Session 維持可恢復的非 closed 狀態。
7. WHILE Session 已為 `closed`，THE Chat API SHALL 以既有 Turn replay 結果，並讓新的 client request 使用新的 active Session。
8. WHEN Flutter App 停止免手持互動、離開對話或切換長者，THE Flutter App SHALL 呼叫 close endpoint，並在 close 尚未收斂時重試同一 close call。

**Main 完成證據：** `backend/src/shared/sessions.py`、`turns.py`、`backend/src/handlers/chat.py`、`session_closer.py`、`backend/tests/test_chat.py`、`test_conversations_data_layer.py`、`app/lib/shared/services/chat_session.dart`、`session_store.dart`、`session_scope_test.dart`、`chat_session_test.dart`。

### Requirement 5：Agent 對話、例行公事與安全事件

**User Story:** 作為長者或照護者，我想讓對話即時更新例行公事並處理潛在安全事件，以便照護行動不必等到每日批次整理。

#### Acceptance Criteria

1. WHEN AgentCore Runtime 判定對話包含 routine create、update、deactivate 或 complete action，THE AgentCore Runtime SHALL 在 Chat API 回應前透過 tools Lambda 寫入對應的版本或 canonical completion event。
2. WHEN realtime routine mutation 已成功提交，THE Chat API SHALL 將 `routines_updated` 設為 `true`，並讓後續 routine 查詢取得新的強一致結果。
3. WHEN 長者對話產生潛在高風險 safety signal，THE AgentCore Runtime SHALL 在回應前呼叫 `notify_caregiver`，建立可冪等收斂的 safety event 並觸發照護者通知。
4. WHEN 同一 safety episode 後續由 batch 提供更多 evidence，THE Batch pipeline SHALL 以相同 canonical event identity 做 revision enrichment，而不建立第二筆 safety event。
5. WHEN Chat API 提供衛教問題，THE AgentCore Runtime SHALL 從 Bedrock Knowledge Base 取得可追溯內容後組成生活與健康資訊回覆，並保留不作醫療診斷的產品邊界。
6. WHEN routine completion 來自對話或手動 endpoint，THE 智慧長照系統 SHALL 以 `elder_id + routine_id + routine_date` 收斂同一 occurrence，並由 canonical completion event 衍生 `done`、`pending` 或 `missed`。
7. IF routine completion request 指定沒有排程的日期，THEN 資料 API SHALL 回傳 `400 ROUTINE_NOT_SCHEDULED`，並保留既有 routine 定義與 completion event。

**Main 完成證據：** `backend/src/agentcore_runtime/`、`backend/src/handlers/tools.py`、`backend/src/shared/routines.py`、`backend/src/handlers/routines.py`、`backend/tests/test_module_b_end_to_end.py`、`test_events_handler.py`、`app/lib/shared/services/routine_sync.dart`、`app/lib/shared/services/notification_service.dart`、`app/test/routine_sync_test.dart`、`notification_schedule_test.dart`。

### Requirement 6：Session 關閉後的事件萃取、去重與恢復

**User Story:** 作為照護者，我想在對話結束後取得整理過的一般生活事件，以便查看可靠且不重複的生活記錄。

#### Acceptance Criteria

1. WHEN Session 完成 close，THE Batch pipeline SHALL 以 immutable ordered snapshot 與 snapshot hash 作為萃取的唯一輸入，並將 normal events 的 materialization 工作送入 SQS。
2. WHEN Batch pipeline 萃取一般生活資訊，THE Direct Seven Pipeline SHALL 依字元上限在 turn 邊界分批、每批一次直接萃取七大高階類別，再以可配置 taxonomy 驗證類別，並以 canonical key、台灣日界與 event slot 規則產生事件。
3. WHILE Batch pipeline 處理同一 frozen snapshot，THE Batch pipeline SHALL 在記憶體內合併指定時間槽內相同 Subject 與 Predicate 的重複資訊，並聯集 evidence conversation IDs。
4. WHEN retry、duplicate delivery、DLQ replay 或人工 replay 重新處理同一 frozen snapshot，THE Direct Seven Pipeline SHALL 產生完全相同的 turn 分批與 canonical event identity，並讓事件以條件式寫入收斂而不重複建立。
5. WHEN Batch pipeline 萃取到疑似 routine completion，THE Batch pipeline SHALL 產生一般事件的 `suspected_routine_id` 標記，並讓 routine occurrence 仍只由 canonical completion event 判定。
6. IF batch worker 遇到 retryable error，THEN SQS consumer SHALL 讓訊息依 retry／redrive 流程重試，並讓 session batch state 保持可恢復狀態。
7. IF DLQ reconciler 收到與 frozen session snapshot hash 相符且尚未 terminal 的訊息，THEN DLQ reconciler SHALL 條件式收斂 batch failed 狀態、清除 lease 並發送安全化告警。
8. WHEN 人工 replay 重新啟動 failed session，THE Batch pipeline SHALL 先以 snapshot hash 將 `failed` 轉回 `pending`，再從 frozen state 重建工作，不重算 snapshot 也不改寫既有事件 identity。
9. WHEN Batch pipeline 建立重複 canonical event，THE Batch pipeline SHALL 以 conditional write 回傳既有結果或記錄衝突，並保持 event identity 與既有事實欄位穩定。

**Main 完成證據：** `backend/src/handlers/batch_extractor.py`、`dlq_reconciler.py`、`session_closer.py`、`backend/src/extraction/`、`backend/tests/test_batch_extractor.py`、`test_extraction_*.py`、`test_conversations_data_layer.py`、`terraform/sqs.tf`、`eventbridge.tf`、`backend/src/shared/db.py`。

### Requirement 7：每日摘要、事件時間軸與照護者資訊

**User Story:** 作為照護者，我想查看有資料完整度標示的摘要、事件、行程與互動統計，以便依線索主動關懷長者。

#### Acceptance Criteria

1. WHEN 排程或照護者呼叫摘要生成 API，THE Daily Summary SHALL 以台灣日界與 input cutoff 產生 overview、固定七類 sections、routines、alerts、interaction count 與 pending session count。
2. WHEN 摘要日期仍有 active、closing 或未完成 batch 的相關 Session，THE Daily Summary SHALL 將 `data_status` 設為 `partial` 並回傳正確的 pending session count。
3. WHEN 摘要日期的相關 closed Session batch 全部完成且 pending session count 為零，THE Daily Summary SHALL 將 `data_status` 設為 `complete`。
4. WHEN 同一日期產生多份摘要，THE Summary generator SHALL 依較新 input cutoff、complete 優先順序與 generated time 條件式收斂摘要版本。
5. WHEN 照護者查詢 events，THE 資料 API SHALL 依台灣日期、事件時間與可選七類 type 回傳穩定分頁的時間軸資料，並以 API 契約欄位呈現事件。
6. WHEN 照護者查詢 stats，THE 資料 API SHALL 以已完成 Chat Turn 數、活動天數、routine occurrence 與 canonical completion event 計算期間統計。
7. WHEN Flutter App 顯示照護者模式，THE 照護者模式 SHALL 提供長者列表、每日摘要、事件時間軸、統計與 routine 管理畫面。
8. WHEN Flutter App 顯示長者模式，THE 長者模式 SHALL 提供今日行程、放大行事曆、免手持 Chat 與照護者綁定流程。

**Main 完成證據：** `backend/src/handlers/summaries.py`、`summary_generator.py`、`daily_digest.py`、`events.py`、`stats.py`、`backend/src/shared/summarizer.py`、`backend/tests/test_events_handler.py`、`test_module_b_end_to_end.py`、`app/lib/caregiver/screens/summaries_screen.dart`、`timeline_screen.dart`、`stats_screen.dart`、`elders_screen.dart`、`app/lib/elder/screens/today_screen.dart`、`calendar_enlarged.dart`、`app/design-system/screenshots/built/`。

### Requirement 8：資料契約、PII 邊界與資料持久化

**User Story:** 作為產品使用者，我想讓對話、事件與照護資料只以必要且受保護的形式保存，以便系統能提供服務並降低個資風險。

#### Acceptance Criteria

1. THE 智慧長照系統 SHALL 以 `docs/api.md` 作為 Flutter App 與 backend 的唯一 API contract，並使用統一 JSON response、錯誤 code、ID prefix、時間格式與分頁規則。
2. THE 智慧長照系統 SHALL 將 elders、conversations、events、daily_summaries 與 routines 依 framework 定義保存於 DynamoDB，並以 caregiver-lookup、elder_accounts 兩張支援表提供 `cg_` 對外識別反查與長者帳號到 `elder_id` 的對應；長期記憶由 AgentCore 託管服務管理。
3. WHEN API 回傳語音音訊，THE 智慧長照系統 SHALL 只回傳短效 presigned URL，並讓 DynamoDB 保存 S3 object key 而非公開 URL。
4. WHEN 系統保存 DynamoDB 或 S3 資料，THE 智慧長照系統 SHALL 啟用傳輸 HTTPS 與靜態加密邊界，並依 PII retention policy 管理保存與刪除。
5. WHEN 系統建立 demo、seed 或測試資料，THE 智慧長照系統 SHALL 使用模擬 persona、合成音訊與非真實健康內容。
6. WHEN API 對外回傳事件、摘要或照護者資料，THE 資料 API SHALL 只公開契約允許欄位，並將 canonical key、extraction track、chunk、revision、evidence internals 與 Cognito `sub` 留在 backend 邊界內。

**Main 完成證據：** `docs/api.md`、`docs/framework.md`、`docs/pii.md`、`backend/src/shared/db.py`、`backend/src/shared/responses.py`、`app/lib/shared/models/`、`app/lib/shared/services/api_client.dart`、`api_repository.dart`、`data/personas/`、`data/scenarios/`、`data/knowledge/`。

### Requirement 9：Terraform 基礎設施與可觀測性

**User Story:** 作為維運者，我想從 Terraform 重現完整 AWS 資源與安全閘門，以便產品行為與部署設定保持一致。

#### Acceptance Criteria

1. THE Terraform IaC SHALL 定義 API Gateway、Cognito、Lambda、SSM config parameters、DynamoDB、S3、SQS、EventBridge、CloudWatch、Bedrock Knowledge Base 與 AgentCore Runtime 所需資源及其 IAM 邊界。
2. WHEN Terraform 的 ASR／TTS endpoint enable flag 為關閉，THE Terraform IaC SHALL 保持自託管 GPU endpoint 不建立，並保留受控 managed provider 的產品路徑。
3. WHEN Terraform 設定自託管 ASR／TTS provider，THE Terraform IaC SHALL 將 route、provider、語言／腔調、approval gate 與 endpoint 能力組合成唯一的 `ASR_CONFIG_JSON` 或 `TTS_CONFIG_JSON` 設定來源。
4. IF 自託管模型的 license、access、capacity、runtime 或 approval gate 未完成，THEN Terraform 與 runtime SHALL 讓該 provider 維持 disabled 或 fail-closed 狀態。
5. WHEN Terraform 啟用 batch、summary、session sweep 或 alerting 資源，THE Terraform IaC SHALL 配置對應的 SQS retry／DLQ、EventBridge schedule、lease、CloudWatch metrics 與 SNS 告警邊界。
6. THE 智慧長照系統 SHALL 以 Terraform `.tf` 與 Terraform lock file 作為交付格式，並讓本機 OpenTofu 驗證不改變交付物的 registry／hash 內容。

**Main 完成證據：** `terraform/providers.tf`、`versions.tf`、`api_gateway.tf`、`cognito.tf`、`lambda.tf`、`lambda_config_parameters.tf`、`dynamodb.tf`、`s3.tf`、`sqs.tf`、`eventbridge.tf`、`cloudwatch.tf`、`agentcore.tf`、`bedrock_kb.tf`、`bedrock_iam.tf`、`asr_lambda_config.tf`、`asr_models.tf`、`tts_lambda_config.tf`、`tts_models.tf`、`terraform/variables.tf`、`terraform/outputs.tf`、`terraform/.terraform.lock.hcl`。

### Requirement 10：產品文件、展示旅程與完成可追溯性

**User Story:** 作為產品審查者，我想從文件與 main 證據追溯長者、照護者、後端與 AWS 資料流，以便確認產品狀態不是由單一頁面或未合併變更推定。

#### Acceptance Criteria

1. THE 智慧長照系統 SHALL 以 `docs/framework.md` 說明架構、資料模型、模組分界、session／batch ownership 與資料寫入規則。
2. THE 智慧長照系統 SHALL 以 `docs/api.md` 說明 `/v1` API endpoints、request／response、錯誤格式、共用 enum 與前後端資料契約。
3. THE 智慧長照系統 SHALL 以 `docs/user-journey.md` 與 `docs/deliverables/user-journey.md` 串接照護者設定、長者語音互動、即時 routine、安全事件、離線整理與照護者追蹤旅程。
4. THE 智慧長照系統 SHALL 以 ASR／TTS framework、model catalog、security 文件與 ADR 記錄 remote-only、語言／腔調路由、PII 邊界及 production approval 狀態。
5. WHEN 審查者以 main commit `7fce81c7e8669ba805541a98fbc0646db9328ac7` 檢查產品，THE 主分支完成證據 SHALL 能從 App、backend、Terraform、data、docs 與 tests 的實際路徑追溯本文件各項能力。
6. THE 智慧長照系統 SHALL 將「不做 ASR／TTS 模型效能測試」視為本 consolidated spec 的範圍界線，並以遠端 provider contract、核准狀態與失敗語意作為語音完成證據。

**Main 完成證據：** `README.md`、`backend/README.md`、`docs/framework.md`、`docs/api.md`、`docs/user-journey.md`、`docs/deliverables/user-journey.md`、`docs/asr/`、`docs/tts/`、`docs/adr/`、`backend/tests/`、`app/test/`。
