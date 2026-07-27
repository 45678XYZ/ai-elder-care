# Implementation Plan: ASR 模型整合（ASR-only）

## Overview

本計畫依 ASR 領域的最小可整合邊界逐步交付：先固定本機開發與測試契約，再建立型別、正規化、設定路由、test-only AWS adapter、facade 與安全遙測，接著完成所有必要的 ASR-only 自動化測試、兩個僅供人工驗證的 Colab 套件與 ADR 範本。所有實作與測試僅限 `backend/src/shared/asr/`、`backend/tests/asr/`、`backend/pyproject.toml` 的開發期依賴、`backend/asr_colab/`，以及 `docs/adr/asr-model-validation-template.md`。

## Tasks

- [ ] 1. 固定 ASR-only 開發期依賴與測試進入點
  - [ ] 1.1 更新 `backend/pyproject.toml` 的 `[project.optional-dependencies].dev`，並建立 `backend/tests/asr/` 的必要測試設定
    - 將 `pytest==8.3.5` 與 `hypothesis==6.122.3` 設為精確、無 marker、無 fallback 的 `[dev]` 開發期依賴；保留 editable install 可解析的後端套件設定。
    - 在 `backend/tests/asr/` 建立共用測試設定，將 Hypothesis profile 固定為每項 property 至少 100 iterations，並提供不建立網路連線的測試防護與共用假件。
    - 將唯一必要安裝與執行指令寫入測試設定註解／helper：先以 `asr-lambda/environment.yml` 建立並啟用 `asr-model` conda 環境，再執行 `python -m pip install -e ".[dev]"` 與 `python -m pytest tests/asr -q`。
    - _Requirements: 8.1, 8.2, 8.12, 8.13_

- [ ] 2. 建立領域型別、錯誤、呼叫 context、deadline/cancel 與受控設定
  - [ ] 2.1 在 `backend/src/shared/asr/` 建立不可變 domain types、typed errors 與 fail-closed config validator
    - 實作並 re-export `InputFormat`、`Language`、`CanonicalAudio`、`Transcript`、`TypedAsrError`、互斥 terminal result、`CorrelationContext`、單調時鐘 `Deadline` 與不可回復 `CancellationSignal`；拒絕空白 audio、未知 language、缺失或無效 correlation context，且 error category 僅可使用需求列舉值。
    - 建立後端擁有的 route/provider/model metadata/AWS capability-gate 設定型別與 parser；缺欄位、未知 schema、矛盾狀態或不可讀 approval record 一律 fail closed。
    - 固定 CE 與 Formo metadata 為 `colab_validation_only`，保存指定 model ID、revision、license、access status、usage restriction 與 approval state；不得將它們建成可供 production invocation 的 provider。
    - 將 Formo Prompt ID validator 限定為六個精確值，拒絕空白、大小寫變形、前後空白與 Unicode lookalike，不做任何猜測或正規化。
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 3.1, 3.5, 3.6, 3.8_

- [ ] 3. 實作 Canonical Audio 邊界並建立規定的 fixture corpus
  - [ ] 3.1 在 `backend/src/shared/asr/canonical_audio.py` 實作 decoder seam 與 canonicalizer，並在 `backend/tests/asr/fixtures/` 建立受控音訊 fixtures
    - 僅接受 `wav`、`m4a` 與非空 audio bytes；以宣告格式驗證容器內容、解碼及精確 frame/sample-rate 時長，將可接受輸入轉為單聲道、16,000 Hz、16-bit signed little-endian PCM 的 `CanonicalAudio`。
    - 對損壞內容、無法解碼或格式宣告與內容不符的輸入回傳 `invalid_audio`；對其他 format 回傳 `unsupported_audio_format`；只能在精確時長大於 60.000 秒時回傳 `audio_duration_exceeded`。
    - 建立短 WAV、有效 M4A、損壞音訊、format/content mismatch、59.999 秒、60.000 秒與 60.001 秒 fixture，並以匿名、安全的測試資料與 manifest 說明預期分類；不得把 source bytes 交給 provider。
    - _Requirements: 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 8.4, 8.5, 8.6_

- [ ] 4. 建立 provider protocol、hak mock 與設定驅動 router
  - [ ] 4.1 在 `backend/src/shared/asr/providers.py`、`hak_mock.py` 與 `router.py` 實作 provider contract、hak route 與路由 precedence
    - 將 `AsrProvider.transcribe` 與 test transport request 的音訊型別限制為 `CanonicalAudio`，並要求 `Language`、`Deadline`、`CancellationSignal` 及 `CorrelationContext`；provider/transport 不得接受原始 WAV/M4A bytes。
    - 實作決定性、非空白 Unicode 的 `HakMockProvider`；其輸出不可依據音訊樣本、呼叫端文字、prompt 或完整逐字稿，且不得建立模型、雲端或網路呼叫。
    - 依固定順序完成 router：language validity → route／CE-Formo production prohibition → capability gate → cancellation → deadline → provider。未知 language 回 `unsupported_language`；hak route 缺失、停用或非 mock provider，以及 CE/Formo 的任何 production route，皆回 `route_not_approved`。
    - _Requirements: 1.1, 1.3, 2.6, 3.1, 3.2, 3.3, 3.4, 3.7_

- [ ] 5. 實作 fail-closed 的 AWS `zh-TW` adapter contract（僅 injected fake transport）
  - [ ] 5.1 在 `backend/src/shared/asr/aws_zh_adapter.py` 實作 capability gate、pre/postflight deadline-cancel checks 與結果正規化
    - adapter 僅接收 Canonical Audio、固定 `zh-TW`、deadline、cancellation 與 correlation context；任何未完整核准的 AWS capability gate 必回 `route_not_approved` 且 transport zero-call。
    - 僅允許 ASR-only composition/test 注入 fake transport。不得新增或 import 實際 AWS service、SDK、network client、endpoint、Region、IAM 或儲存服務實作，且不得執行網路呼叫。
    - 在 transport 前後檢查 cancellation 與單調 deadline，使取消與逾期分別收斂為 `cancelled` 與 `deadline_exceeded`，不讓成功結果覆蓋終態。
    - 將空白／非文字／malformed transport 結果正規化為 `provider_invalid_response`；已知 unavailable 正規化為 `provider_unavailable`；未分類例外不外洩 exception text 並正規化為 `provider_failure`；一次合格 invocation 最多呼叫 transport 一次。
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9_

- [ ] 6. 組合 ASR facade、終態安全遙測與 evidence validation
  - [ ] 6.1 在 `backend/src/shared/asr/` 實作 facade、單次 terminal telemetry emitter 與 evidence/ADR validation utilities
    - facade 只接受規定的 six inputs，依序協調 input gate、canonicalizer、router 與 provider，並只回傳非空白 `Transcript` 或 `TypedAsrError`；不理解 HTTP、資料庫或對話工作流。
    - 實作每一 correlation context 恰一筆的 terminal telemetry，鍵嚴格限於 allowlist；投影 `terminal_outcome`、`deadline_outcome`、非負 `elapsed_ms`、適用 error category 與 retryable，且絕不保存 audio、PCM samples、完整 transcript、token、個資、raw response、raw exception 或 Formo Prompt ID。
    - 實作結構化 evidence validator 與 ADR evidence-reference projection：驗證 success/failure 條件、required fields、redaction 禁止欄位，以及僅允許 `run_id`、`model_id`、`input_fixture_id`、`outcome`、`failure_category` 的 ADR 引用。
    - _Requirements: 1.2, 1.3, 5.1, 5.2, 5.3, 5.4, 5.5, 7.1, 7.2, 7.3, 7.5_

- [ ] 7. 撰寫所有必要的 ASR-only unit、fixture 與 property tests
  - [ ] 7.1 在 `backend/tests/asr/` 撰寫 domain、設定、router、provider、hak mock、AWS adapter、facade、telemetry、evidence 與 ADR template validator 的 unit/fixture tests
    - 使用 task 3 的完整 fixture corpus 驗證四個成功案例（short WAV、valid M4A、59.999 秒、60.000 秒）產生正確 Canonical Audio，並驗證 corrupt/mismatch 為 `invalid_audio`、60.001 秒為 `audio_duration_exceeded`。
    - 覆蓋 typed errors、config fail-closed、provider canonical-only contract、hak deterministic output、AWS fake-transport mapping、facade terminal union、single-emission telemetry、evidence schema 與 ADR mandatory-heading/reference validation。
    - 所有 tests 必須是必要項目，不得標示 optional；不得建立實際 AWS service/SDK/network 呼叫、真實模型呼叫、完整對話測試或任何非 ASR-only 測試。
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 4.1, 4.4, 4.5, 4.7, 4.8, 5.1, 5.2, 5.3, 5.4, 5.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.3, 8.4, 8.5, 8.6, 8.12, 8.13_

  - [ ] 7.2 在獨立的 `backend/tests/asr/test_property_route_approval.py` 撰寫 fail-closed 路由與核准 property test
    - **Property 1: 未核准路由永遠 fail closed。**
    - 以至少 100 iterations 生成 routes、provider states、capability-gate 欄位與 unknown languages，驗證任一缺少 gate 核准或 CE/Formo production route 均為 `route_not_approved` 且 fake transport zero-call；unknown language 必為 `unsupported_language` 且 zero-call。
    - _Validates: Requirements 3.4, 3.7, 4.4, 4.5, 8.7; Design Property 1_

  - [ ] 7.3 在獨立的 `backend/tests/asr/test_property_aws_adapter.py` 撰寫 fake transport、deadline、cancellation 與 error-normalization property test
    - **Property 2: AWS adapter 的 fake transport 邊界、deadline 與錯誤正規化。**
    - 以至少 100 iterations 生成 Canonical Audio、deadline/cancellation 時點與 fake transport result，驗證只傳遞允許欄位、preflight/postflight precedence、zero-call gate，以及 blank/malformed/exception 分別收斂為定義的 Typed ASR Error。
    - _Validates: Requirements 2.6, 4.1, 4.6, 4.7, 4.8, 8.8; Design Property 2_

  - [ ] 7.4 在獨立的 `backend/tests/asr/test_property_telemetry.py` 撰寫終態 telemetry 唯一性與去識別化 property test
    - **Property 3: 終態 telemetry 唯一且不含敏感內容。**
    - 以至少 100 iterations 生成 success、cancelled、deadline-exceeded 與 error terminal outcomes，以及 audio/transcript/token/個資/raw-response/Formo Prompt ID sentinels；驗證每個 correlation context 恰有一個 terminal result 和一筆 allowlist-only telemetry。
    - _Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 8.9; Design Property 3_

  - [ ] 7.5 在獨立的 `backend/tests/asr/test_property_formo_prompt.py` 撰寫 Formo Prompt ID 精確 allowlist property test
    - **Property 4: Formo Prompt ID 精確 allowlist。**
    - 以至少 100 iterations 生成任意 Unicode candidate，驗證設定與 Colab input 當且僅當候選精確等於六個允許值才通過；任何拒絕或接受路徑均不可將 prompt 寫入 telemetry、evidence 或 ADR projection。
    - _Validates: Requirements 3.8, 6.6, 8.10; Design Property 4_

  - [ ] 7.6 在獨立的 `backend/tests/asr/test_property_evidence_adr.py` 撰寫 evidence/ADR schema、終態一致性與 redaction property test
    - **Property 5: Evidence/ADR schema、終態一致性與去識別化。**
    - 以至少 100 iterations 生成 success/failure evidence records 與 ADR references，驗證 success 的 transcript presence/count、failure 的 prerequisite/category、required fields、敏感欄位拒絕與五欄 evidence projection；包含將於 task 9 建立的 ADR template schema check。
    - _Validates: Requirements 6.5, 6.10, 7.1, 7.2, 7.3, 7.5, 8.11; Design Property 5_

- [ ] 8. 建立僅人工驗證的 Colab common contract、CE 與 Formo 套件
  - [ ] 8.1 在 `backend/asr_colab/` 建立兩模型共用的 Colab validation contract、匿名 fixture provenance 與安全 evidence 輸出規則
    - 建立 common README／schema assets，規定每份 evidence 的 required fields、manifest digest、redaction version、success/failure 規則、禁止欄位與只接受 synthetic 或合法公開授權音檔的 provenance 格式。
    - 定義兩個 package 都必須提供精確 `package==version` dependency manifest、免費 GPU runtime 選擇、WAV 輸入、M4A decode、前置條件失敗分類及可執行 retry step；不得將 token、完整 transcript 或 audio bytes 寫入 notebook source/output、evidence 或 template。
    - 明定 CE/Formo 套件僅供手動 Colab validation，絕不作 production invocation，且不得呼叫 task 5 的 adapter 或任何實際 AWS service/SDK/network。
    - _Requirements: 6.1, 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11, 7.1, 7.2, 7.3_

  - [ ] 8.2 在 `backend/asr_colab/taiwan_tongues_asr_ce/` 建立 Taiwan-Tongues-ASR-CE 手動驗證 package
    - 建立 `validation.ipynb`、README、全精確版本的 `requirements.lock`、fixture provenance 與 evidence schema；固定記錄 `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0`、`zh`／`hak`、license `other` 及 `colab_validation_only`。
    - notebook 必須以人工選擇免費 GPU 為前提，提供 synthetic/authorized WAV 與 M4A decode 流程、GPU/dependency/download/decoder preflight、redacted structured evidence，並對每項前置條件失敗輸出分類與 retry step。
    - 不得在 package 中執行或宣稱 CE 的 production invocation。
    - _Requirements: 3.5, 3.6, 6.1, 6.2, 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11_

  - [ ] 8.3 在 `backend/asr_colab/formospeech_whisper_v3/` 建立 FormoSpeech Whisper-v3 手動驗證 package
    - 建立 `validation.ipynb`、README、全精確版本的 `requirements.lock`、fixture provenance 與 evidence schema；固定記錄 `formospeech/whisper-large-v3-taiwanese-hakka`、`CC BY-NC 4.0`、gated-model prerequisite 與 `colab_validation_only`。
    - 僅從 Colab Secret 或短生命週期 runtime environment 讀取 token；不將 token 寫入 notebook、output、lockfile、evidence 或 ADR。開始轉寫前使用 task 2 的 exact validator 檢查 Formo Prompt ID，並提供 WAV/M4A、GPU/dependency/download/gated-access/token/decoder failure 的 retry steps。
    - 不得在 package 中執行或宣稱 Formo 的 production invocation。
    - _Requirements: 3.5, 3.6, 3.8, 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11_

- [ ] 9. 建立 ADR template 並使其通過既有 schema checks
  - [ ] 9.1 建立 `docs/adr/asr-model-validation-template.md`，以 task 6 validator 與 task 7 tests 驗證其 heading 與 evidence-reference schema
    - 提供 `title`、`status`、`date`、`owners`、`scope`、`candidate_models`、`evidence_references`、`aws_capability_gate_status`、`decision`、`rationale`、`risks`、`non_goals`、`follow_up_actions` 等必要 section。
    - 僅允許 ADR evidence reference 使用 `run_id`、`model_id`、`input_fixture_id`、`outcome`、`failure_category`；不得寫入完整 transcript、token、audio、prompt、raw provider response 或其他敏感內容。
    - 在 non-goals 明確標示 Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 的 production invocation 為本期禁止，並將 AWS capability gate 保持為外部核准紀錄，不推定服務、Region 或 deployment decision。
    - _Requirements: 7.4, 7.5, 7.6, 8.3, 8.11_

- [ ] 10. Checkpoint — 執行必要 ASR-only pytest 驗證並交接人工 Colab gate
  - Ensure all tests pass, ask the user if questions arise.
  - [ ] 10.1 在 `backend/` 以指定命令執行必要、非 optional 的 ASR-only suite，修正本計畫範圍內任何失敗後重跑至通過
    - 依序執行 `python -m pip install -e ".[dev]"` 與 `python -m pytest tests/asr -q`；確認 unit、fixture 與五類 property tests 均執行，任何失敗皆使驗證非零結束。
    - 驗證 suite 不建立實際 AWS service、SDK 或 network 呼叫，不執行真實模型或 production invocation，且不包含完整對話測試。
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 8.13_

  - [ ] 10.2 透過 `backend/asr_colab/` 的 README/notebook instructions 交接不可自動化的人工 Colab gate，但不在 CI 或本任務中實際呼叫模型
    - 人工驗證者須在免費 Colab 選擇 GPU、只上傳 synthetic/authorized WAV 或 M4A、確認 fixture provenance、執行依賴／下載／decoder preflight，並檢查 redacted evidence 是否符合 schema。
    - Formo 額外須確認 gated-model access 與 token 只存在 runtime secret；CE/Formo 任一前置條件失敗必依 package 的 failure category 與 retry step 處理。
    - 手動 gate 的輸出僅能作為 ADR evidence；不得開啟 production invocation、實際 AWS service/SDK/network 或任何部署流程。
    - _Requirements: 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

## Notes

- 所有測試任務均為必要項目，沒有 optional `*` 任務；property tests 依設計的五項正確性質各自獨立，且每項至少 100 iterations。
- 任務只可變更 Overview 所列的五個 ASR-only 位置。不得納入公開對話契約、request/idempotency/session、Agent/Bedrock/routine、Flutter、部署、基礎設施或儲存相關工作。
- AWS `zh-TW` 僅是 fail-closed contract 加 injected fake transport：禁止 actual AWS service、SDK 或 network。CE/Formo 僅限手動 Colab validation，禁止 production invocation；ASR-only suite 不得有完整對話測試。
- Task 10 的 pytest 為必要自動化驗證；Colab 僅為明確交接的人工 gate，不得由 CI、自動化測試或本機測試程序執行。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["4.1"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["6.1"] },
    { "id": 6, "tasks": ["7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3", "7.4", "7.5", "7.6"] },
    { "id": 8, "tasks": ["8.1"] },
    { "id": 9, "tasks": ["8.2", "8.3"] },
    { "id": 10, "tasks": ["9.1"] },
    { "id": 11, "tasks": ["10.1"] },
    { "id": 12, "tasks": ["10.2"] }
  ]
}
```
