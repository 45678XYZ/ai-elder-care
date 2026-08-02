# Implementation Plan：main 已完成產品狀態

## 定位與執行界線

本文件是依 Git `main` commit `7fce81c7e8669ba805541a98fbc0646db9328ac7` 反向整理的**完成證據確認／追溯清單**，不是新的 implementation backlog。每個任務都只確認指定 main 證據與 requirements/design 的對應，不得新增、重構或修改產品程式碼、Terraform、data 或既有產品文件。

本階段不執行任何 implementation task、不跑測試、不執行 Terraform apply/destroy，也不新增或執行 ASR／TTS 模型效能測試。語音相關任務只追溯 remote-only route、provider contract、approval gate、typed failure、fallback 與 PII redaction 的既有證據。外層 orchestrator 可在完成追溯後將本清單任務標記為 completed。

## Tasks

- [x] 1. Flutter App 完成證據確認
  - [x] 1.1 確認同意、登入、Cognito claim 角色分流、登出與受保護 route 清除的 main 證據
    - 追溯 `app/lib/app_router.dart`、shared auth/consent screens 與對應 `app/test/` 證據；只確認既有行為，不修改 Dart 程式碼。
    - _Requirements: 1.1–1.5；Design: 3.1、3.2、8.1_
  - [x] 1.2 確認長者模式與照護者模式的畫面、服務及 user journey 已由 main 覆蓋
    - 追溯 elder 今日行程、calendar、Chat、caregiver link，以及 caregiver elders、summary、timeline、stats、routine UI；只建立需求到路徑的對應。
    - _Requirements: 2.4、7.7、7.8、10.3；Design: 3.1、3.7、8.1_
  - [x] 1.3 確認 Flutter API models、API repository、audio/notification、client request ID 與 session close retry 符合既有契約
    - 核對 nullable `reply_audio_url`、穩定 `error.code`、新 request ID／重試沿用同一 ID、close `REQUEST_IN_PROGRESS` retry 與 elder scope 切換證據；不新增測試或產品程式碼。
    - _Requirements: 3.1、4.7、4.8、8.1；Design: 3.1、3.3、3.5_

- [x] 2. Backend／realtime 完成證據確認
  - [x] 2.1 確認 API ingress、Cognito authorization、共通 response 與資料 handler 的 main 證據
    - 追溯 `backend/src/shared/auth.py`、`responses.py`、elders/caregivers/events/routines 等 handlers 及其測試；確認 401、403/404、ID prefix、時間與分頁由契約統一管理。
    - _Requirements: 1.2–1.5、2.4、8.1；Design: 3.2、4.2、8.1_
  - [x] 2.2 確認 elder profile、health notes 與 caregiver binding 的 server-owned 寫入證據
    - 追溯 `elders.py`、`models.py` 及既有 elders、health notes、link tests；確認 `eld_`／`cg_`、defaults、atomic note mutation、首次 `linked_at` 與 ownership 保護。
    - _Requirements: 2.1–2.5；Design: 4.2、8.1、8.2_
  - [x] 2.3 確認 Property 1「Server-owned 長者資料不變量」的既有 domain/test 證據
    - 核對 client 不可覆寫 ID、creator binding、`created_at`、defaults 與成功 mutation 才更新 `updated_at`；此任務只追溯 main 證據，不新增或執行 property test。
    - _Requirements: 2.1、2.5；Design: Property 1、4.2_
  - [x] 2.4 確認 Property 2「照護者綁定冪等且保留首次時間」的既有 domain/test 證據
    - 核對重複 binding 最多保留一筆 `cg_` 關係、首次 `linked_at` 不變且 replay 既有結果；不修改 binding implementation。
    - _Requirements: 2.3；Design: Property 2、4.2_
  - [x] 2.5 確認 chat realtime ordering、audio/text convergence、terminal commit 與 TTS nullable audio 的 main 證據
    - 追溯 `backend/src/handlers/chat.py`、shared ASR/TTS facade、turn/session modules 與既有 Chat tests；確認音訊只進同一 realtime path，文字 business commit 不受 TTS 全敗回滾。
    - _Requirements: 3.1–3.8；Design: 3.3、6、7_
  - [x] 2.6 確認 Property 3「Audio canonicalization 與同一路徑」的既有 domain/test 證據
    - 核對可接受 audio 會成為 mono、16 kHz、S16LE PCM，非空 final transcript 與 text 共用 orchestration，超過 60 秒不進 provider；不執行音訊效能或模型測試。
    - _Requirements: 3.2；Design: Property 3、3.3、6_
  - [x] 2.7 確認 Property 4「語言與腔調 snapshot 穩定」的既有 domain/test 證據
    - 核對 `lang=hak` 只讀 elder profile 六腔 `hakka_dialect`，reserve 後 route 使用 snapshot，request body 與後續 profile 變更不得覆寫該 snapshot。
    - _Requirements: 3.4；Design: Property 4、3.3、4.2_
  - [x] 2.8 確認 Property 5「語音 provider gate 與同語言 fail-closed」的既有 contract 證據
    - 核對 managed route、已核准自託管 route、license/access/capacity/runtime/approval gate 與同語言 fallback；只確認設定與失敗語意，不新增、部署或 benchmark provider。
    - _Requirements: 3.3、3.7、9.2–9.4；Design: Property 5、3.3、5、6_
  - [x] 2.9 確認 Property 6「TTS 失敗不回滾文字 business commit」的既有 domain/test 證據
    - 核對 TTS 成功／失敗／逾時序列下 Turn 仍完成、全敗回傳 `reply_audio_url=null` 且不重複 routine/event side effect；不跑 provider 效能測試。
    - _Requirements: 3.5、3.6；Design: Property 6、3.3、7_
  - [x] 2.10 確認 Property 7「Telemetry 與公開 response 的敏感欄位封閉性」的既有 serializer/telemetry 證據
    - 核對 telemetry 與公開 response 的 allowlist 不包含音訊、transcript、reply text、token、PII、endpoint、raw provider response 或 extraction internals。
    - _Requirements: 3.8、8.6；Design: Property 7、6、8.2_

- [x] 3. AgentCore／routine／safety 完成證據確認
  - [x] 3.1 確認 AgentCore Runtime、tools Lambda、RAG 與 routine/safety ownership 的 main 證據
    - 追溯 `backend/src/agentcore_runtime/`、`handlers/tools.py`、`shared/routines.py`、Knowledge Base 及 Module B end-to-end evidence；確認對話大腦不在一般 Lambda，side effect 由 authenticated tools scope 提交。
    - _Requirements: 5.1–5.7；Design: 2.1、2.2、3.4、4.2、7_
  - [x] 3.2 確認 Property 12「Safety event cross-track enrichment」的既有 domain/test 證據
    - 核對 realtime safety event 與後續 batch evidence 使用同一 event identity，只條件式增加 revision/detail/confidence/evidence，不建立第二筆 safety event。
    - _Requirements: 5.3、5.4；Design: Property 12、3.4、4.2、7_
  - [x] 3.3 確認 Property 13「Routine occurrence canonical identity」的既有 domain/test 證據
    - 核對 `elder_id + routine_id + routine_date` 收斂對話與手動 completion，canonical completion event 決定 done，沒有 event 才依 schedule/cutoff/grace 判定。
    - _Requirements: 5.6、5.7；Design: Property 13、3.4、4.2、7_

- [x] 4. Session／batch／extraction 完成證據確認
  - [x] 4.1 確認 Turn idempotency、Session state machine、immutable close 與 recovery ownership 的 main 證據
    - 追溯 `sessions.py`、`turns.py`、`session_closer.py`、`chat.py` 與 App session store；確認 elder scope/hash lookup 先於 reserve、`active→closing→closed`、snapshot freeze 與新 active session 分流。
    - _Requirements: 4.1–4.8；Design: 3.3、3.5、4.2、7_
  - [x] 4.2 確認 Property 8「Turn routing 先查冪等再 reserve」的既有 domain/test 證據
    - 核對既有 Turn 查詢／hash 判定完成前不得選 session 或 reserve，processing Turn 不得重複 reserve。
    - _Requirements: 4.1、4.3、4.4；Design: Property 8、3.3、3.5_
  - [x] 4.3 確認 Property 9「Terminal Turn replay 冪等」的既有 domain/test 證據
    - 核對相同 scope、request ID、request hash 的 completed/failed Turn 可重播相同 terminal result、conversation/session identity 與錯誤，且 business side effect 不增加。
    - _Requirements: 4.2；Design: Property 9、3.3、3.5_
  - [x] 4.4 確認 Property 10「Session close 的 immutable state machine」的既有 domain/test 證據
    - 核對無 inflight 且 snapshot 完整才可 freeze、設 batch pending 並完成 close；有 inflight 或 snapshot 不完整時保留可恢復非 closed 狀態。
    - _Requirements: 4.5、4.6；Design: Property 10、3.5、4.2、7_
  - [x] 4.5 確認 Property 11「Closed Session replay 與新 request 分流」的既有 domain/test 證據
    - 核對 closed session 只 replay 既有 Turn，新的 client request ID 不追加 closed session 而導向新的 active session。
    - _Requirements: 4.7；Design: Property 11、3.5_
  - [x] 4.6 確認 immutable snapshot、chunk manifest、SQS retry/DLQ、extraction 與 replay recovery 的 main 證據
    - 追溯 `batch_extractor.py`、`dlq_reconciler.py`、`extraction/`、`db.py`、SQS/EventBridge Terraform 與既有 batch/extraction tests；只確認 manifest、lease、conditional write、retry/redrive、hash guard 與 replay ownership。
    - _Requirements: 6.1–6.9；Design: 3.5、3.6、4.2、7_
  - [x] 4.7 確認 Property 14「Batch event canonicalization 與台灣日界」的既有 domain/test 證據
    - 核對 canonical event 僅依台灣日期、固定 slot、normalized Subject/Predicate 與 taxonomy version identity，不依賴 chunk、track、模型版本或 detail。
    - _Requirements: 6.2；Design: Property 14、3.6、4.2_
  - [x] 4.8 確認 Property 15「Frozen snapshot 內記憶體去重」的既有 domain/test 證據
    - 核對同一時間槽與 Subject/Predicate 合併、detail 取最完整、evidence conversation IDs 聯集，且不同 slot、不同欄位與 context-only turn 不誤合併。
    - _Requirements: 6.3；Design: Property 15、3.6、4.2_
  - [x] 4.9 確認 Property 16「Chunk manifest retry reuse」的既有 domain/test 證據
    - 核對首次成功保存的 manifest、core ranges、ordinal、chunk IDs 在 retry、duplicate、DLQ replay、manual replay 中完全重用，不重新分配 core turns。
    - _Requirements: 6.4、6.8；Design: Property 16、3.6、7_
  - [x] 4.10 確認 Property 17「Batch 不擁有 routine completion」的既有 domain/test 證據
    - 核對疑似 routine completion 只能寫 normal event 的 `structured_detail.suspected_routine_id`，不可建立 completion event、修改 routine version 或 occurrence 狀態。
    - _Requirements: 6.5；Design: Property 17、2.2、3.6、4.2_
  - [x] 4.11 確認 Property 18「Canonical event conditional write」的既有 domain/test 證據
    - 核對 identical duplicate、合法 safety enrichment、互斥 payload 與 retry 分別回既有結果、保留 identity、依 revision 更新或記錄 conflict，不靜默覆寫事實。
    - _Requirements: 6.9；Design: Property 18、3.4、3.6、4.2、7_

- [x] 5. Summary／events／stats 完成證據確認
  - [x] 5.1 確認 Daily Summary 生成、七類 sections、data status、cutoff 與 routine projection 的 main 證據
    - 追溯 `summaries.py`、`summary_generator.py`、`daily_digest.py`、`shared/summarizer.py` 與 Module B tests；確認 EventBridge/API 兩入口及 `partial`／`complete` 語意。
    - _Requirements: 7.1–7.4；Design: 3.7、4.2、7_
  - [x] 5.2 確認 Property 19「Summary data-status 完整分類」的既有 domain/test 證據
    - 核對 active/closing 或 pending/processing/failed batch 導致 `partial` 與精確 pending count，只有全部相關 closed batch completed 才為 `complete`。
    - _Requirements: 7.1–7.3；Design: Property 19、3.7、7_
  - [x] 5.3 確認 Property 20「Summary winner 收斂規則」的既有 domain/test 證據
    - 核對 summary winner 依較新 cutoff、complete 優先、同完整度較新 generated time 條件式收斂，不被較舊或較低完整度候選覆寫。
    - _Requirements: 7.4；Design: Property 20、3.7、4.2、7_
  - [x] 5.4 確認 events timeline API、stable pagination 與 caregiver read model 的 main 證據
    - 追溯 `events.py`、events-by-time query、API contract 與 caregiver timeline UI；確認台灣日期邊界、時間排序、type filter、opaque token 與公開欄位。
    - _Requirements: 7.5、8.6；Design: 3.7、4.2、8.2_
  - [x] 5.5 確認 Property 21「Event timeline projection 與穩定分頁」的既有 domain/test 證據
    - 核對固定 `event_time_key` 排序／分頁與 API allowlist，且 canonical key、track、chunk、revision、evidence 等 backend internals 不外洩。
    - _Requirements: 7.5、8.6；Design: Property 21、3.7、4.2、8.2_
  - [x] 5.6 確認 stats reducer、canonical completion facts 與照護者 stats UI 的 main 證據
    - 追溯 `stats.py`、stats tests 與 caregiver stats screen；確認統計不以 summary 取代 canonical facts。
    - _Requirements: 7.6；Design: 3.7、4.2、7_
  - [x] 5.7 確認 Property 22「Stats 只計 canonical completed facts」的既有 domain/test 證據
    - 核對只計 completed Chat Turns、active days、daily zero-fill、routine occurrence 與 canonical completion count，重複 completion 不增加計數。
    - _Requirements: 7.6；Design: Property 22、3.7、4.2_

- [x] 6. Terraform／security 完成證據確認
  - [x] 6.1 確認 Terraform AWS resource inventory、IAM boundary、queues/schedules/observability 與 lock file 的 main 證據
    - 追溯 API Gateway、Cognito、Lambda、DynamoDB、S3、SQS、EventBridge、CloudWatch、Bedrock KB、S3 Vectors、AgentCore、SNS 及 `terraform/.terraform.lock.hcl`；不執行 apply/destroy。
    - _Requirements: 9.1、9.5、9.6；Design: 5、8.1、8.2_
  - [x] 6.2 確認 Property 23「Terraform config serialization 唯一來源」的既有 contract 證據
    - 核對 `ASR_CONFIG_JSON`／`TTS_CONFIG_JSON` 序列化可保留 route、provider、language/dialect、approval、capability matrix，且 runtime 不依賴散落設定；不新增或執行模型測試。
    - _Requirements: 9.3；Design: Property 23、5、6_
  - [x] 6.3 確認 PII、encryption、retention、synthetic data、managed memory 與 observability redaction 的 main 證據
    - 追溯 `docs/pii.md`、DynamoDB/S3 Terraform、`data/` synthetic fixtures、shared db/telemetry 與 AgentCore memory boundary；確認不得保存真實長者聲音、逐字稿、個資或健康資料。
    - _Requirements: 8.2–8.5、9.1、9.5；Design: 4、5、6、8.2_
  - [x] 6.4 確認 Property 24「Summary／event／API model 的公開欄位封閉」的既有 serializer/contract 證據
    - 核對 API 僅輸出 `docs/api.md` 允許欄位、enum、時間格式與 opaque token，內部 identity、revision、chunk、taxonomy、evidence、Cognito sub、storage key 不外洩。
    - _Requirements: 8.1、8.6；Design: Property 24、3.2、4.2、8.2_

- [x] 7. Docs／provenance 完成證據確認
  - [x] 7.1 確認 framework、API、user journey、ASR/TTS、security、ADR 與 deliverables 文件互相對應
    - 追溯 `docs/framework.md`、`docs/api.md`、`docs/user-journey.md`、`docs/deliverables/user-journey.md`、`docs/asr/`、`docs/tts/`、`docs/adr/` 與 README/backend README；不新增文件內容。
    - _Requirements: 10.1–10.4；Design: 1.1、8.1、10_
  - [x] 7.2 確認 `main` commit provenance 與證據範圍未混入工作樹、`eval/` 或未合併分支
    - 以 requirements/design 所列的 source、tests、Terraform、data、docs 路徑建立完成證據索引；只採認 `7fce81c7e8669ba805541a98fbc0646db9328ac7`，不以現況未合併內容補證。
    - _Requirements: 10.5；Design: 1、8.1、8.2、10_
  - [x] 7.3 確認本 consolidated spec 的非目標與語音範圍界線已被保留
    - 核對追溯結果不宣稱 AWS 實際可用性、模型品質或性能，不新增 ASR/TTS benchmark；語音完成證據限於 remote-only、語言／腔調一致、approval gate、typed error、fallback 與 PII redaction。
    - _Requirements: 10.6；Design: 1.2、3.3、5、6、8.2_

## Checkpoints

- [x] Checkpoint A：完成 Flutter、backend/realtime、AgentCore 與 session/batch 的 main 證據對照；不得執行測試或修改產品檔案。
- [x] Checkpoint B：完成 summary/events/stats、Terraform/security 與 docs/provenance 的需求追溯；只確認既有完成證據。
- [x] Final checkpoint：確認所有任務都只描述 main 完成證據的確認／追溯，沒有 implementation backlog、ASR/TTS 模型效能測試、產品程式碼修改或 Terraform apply/destroy。

## Notes

- 所有任務均以「確認／追溯 main 已完成證據」為動詞；不是要求重新實作功能。
- 任務中的 Property 1–24 來自 design.md 的 Correctness Properties；本文件只要求核對既有 domain/unit/integration/contract 證據，不要求新增或執行 property-based test。
- Requirements 1–10 均以 `_Requirements` 標記追溯；Design section/property 以 `_Design` 標記追溯。
- 語音任務只確認 remote-only、route、approval、typed failure、fallback 與 telemetry redaction；不得轉成 ASR／TTS 模型效能或品質評測。
- 不建立新產品程式碼、不改 Terraform/data/docs、不執行測試、不執行部署；外層 orchestrator 可在完成證據確認後統一標記任務完成。

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": 0,
      "tasks": ["1.1", "2.1", "3.1", "4.1", "5.1", "5.4", "5.6", "6.1", "6.3", "7.1", "7.3"]
    },
    {
      "id": 1,
      "tasks": ["1.2", "1.3", "2.2", "2.5", "3.2", "3.3", "4.6", "5.2", "5.3", "6.2", "6.4", "7.2"]
    },
    {
      "id": 2,
      "tasks": ["2.3", "2.4", "2.6", "2.7", "2.8", "2.9", "2.10", "4.2", "4.3", "4.4", "4.5", "4.7", "4.8", "4.9", "4.10", "4.11", "5.5", "5.7"]
    }
  ]
}
```
