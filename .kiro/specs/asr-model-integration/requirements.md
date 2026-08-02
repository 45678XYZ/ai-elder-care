# 需求規格

## 簡介

本文件定義「ASR 模型整合」本期唯一交付：位於 `backend/src/shared/asr/`、可由既有呼叫端獨立整合的 ASR 領域邊界、設定、路由器與 Provider 合約；`hak` mock；尚未選定服務的 AWS `zh-TW` Adapter Contract；安全遙測；Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 的免費 Colab 驗證包、結構化證據與 ADR 範本；以及必做的 ASR-only 自動化測試。

本期不修改或驗證公開 Chat API、Bedrock Agent、資料交易、持久化、Flutter 或 AWS 部署。公開 API 與 `docs/api.md` 維持外部責任，且不在本期變更。

## 詞彙表

- **ASR 領域套件**：`backend/src/shared/asr/` 內可獨立整合的後端套件，提供領域型別、設定、音訊正規化、路由與 Provider 合約。
- **ASR 呼叫端**：呼叫 ASR 領域套件的既有後端程式；ASR 呼叫端負責提供已驗證的 Audio Bytes、Input Format、Language 與 Correlation Context。
- **Audio Bytes**：ASR 呼叫端已完成來源驗證後交給 ASR 領域套件的原始音訊位元組。
- **Input Format**：ASR 呼叫端宣告的來源音訊格式，值為 `wav` 或 `m4a`。
- **Language**：ASR 呼叫端宣告的辨識語言，值為 `zh-TW` 或 `hak`。
- **Correlation Context**：含不可包含音訊、逐字稿、HF Token 或長者身分資料之不透明 `correlation_id` 的呼叫關聯資訊。
- **Canonical Audio**：ASR 領域套件完成解碼後產生的單聲道、16,000 Hz、16-bit signed little-endian PCM 音訊與精確時長資料。
- **Audio Canonicalizer**：將 Audio Bytes 與 Input Format 驗證、解碼並轉換為 Canonical Audio 的 ASR 領域套件元件。
- **ASR Router**：依 Language、後端 ASR 設定與核准狀態選擇 Provider Contract 或回傳 Typed ASR Error 的 ASR 領域套件元件。
- **後端 ASR 設定**：僅由後端管理、宣告 Language 路由、Provider 狀態、模型中繼資料與核准狀態的設定資料。
- **Provider Contract**：接收 Canonical Audio、Language、Deadline、Cancellation Signal 與 Correlation Context，並回傳 Transcript 或 Typed ASR Error 的可替換介面。
- **Transcript**：Provider Contract 成功辨識後回傳的非空白 Unicode 文字。
- **Typed ASR Error**：ASR 領域套件定義的具型別錯誤結果，包含固定錯誤分類、可供內部診斷的安全訊息與可選 retryable 狀態。
- **ASR Error Category**：`invalid_audio`、`unsupported_audio_format`、`audio_duration_exceeded`、`unsupported_language`、`route_not_approved`、`deadline_exceeded`、`cancelled`、`provider_unavailable`、`provider_invalid_response` 或 `provider_failure` 其中之一。
- **Deadline**：由 ASR 呼叫端提供的單調時鐘到期時刻，Provider Contract 必須在到期時終止處理。
- **Cancellation Signal**：由 ASR 呼叫端或 Deadline 觸發、用於要求 Provider Contract 停止處理的取消訊號。
- **AWS `zh-TW` Adapter Contract**：`zh-TW` 路徑的 Provider Contract 定義；本期不選定 AWS 服務或 Region，且不具備真實 AWS 外呼實作。
- **Injected Fake Transport**：僅在 ASR-only 自動化測試中注入 AWS `zh-TW` Adapter Contract 的假傳輸實作，不會建立網路、SDK 或雲端服務呼叫。
- **AWS Capability Gate**：部署負責人以可追溯核准紀錄確認 Region 的 `zh-TW` 支援、服務輸入與輸出模式、Canonical PCM 相容性、Timeout 與 Cancellation、IAM、S3 必要性，以及 S3 結果與清理需求後才成立的外部能力閘門。
- **部署負責人**：負責驗證與核准 AWS Capability Gate、部署資源、IAM 與資料儲存責任的指定人員。
- **hak Mock Provider**：在 `hak` 路徑以決定性安全測試文字回傳 Transcript 的 Provider Contract 實作，不呼叫模型、網路或雲端服務。
- **模型中繼資料**：Taiwan-Tongues-ASR-CE 或 FormoSpeech Whisper-v3 的模型識別、版本或 revision、授權、存取狀態、用途限制與核准狀態。
- **Production Invocation**：由正式後端處理真實使用者音訊並呼叫模型或外部 ASR 服務的行為。
- **Formo Prompt ID**：FormoSpeech Whisper-v3 接受的客語腔調值之一：`htia_sixian`、`htia_hailu`、`htia_dapu`、`htia_raoping`、`htia_zhaoan` 或 `htia_nansixian`。
- **Safe Telemetry**：只記錄允許欄位的結構化終態紀錄；允許欄位為 `correlation_id`、`language`、`route`、`provider_id`、`input_format`、`canonical_sample_rate_hz`、`canonical_channels`、`audio_duration_ms`、`deadline_outcome`、`terminal_outcome`、`error_category`、`elapsed_ms` 與 `retryable`。
- **Colab 驗證包**：可於免費 Google Colab 手動執行的模型專屬內容，包含已精確釘選版本的安裝清單、WAV/M4A 流程、前置條件檢查、結果證據與資料處理限制。
- **合成測試音檔**：不含真實長者資料、由專案建立或已獲合法公開使用授權的 WAV 或 M4A 音檔。
- **結構化證據紀錄**：Colab 驗證產出的 JSON 或 JSON Lines 紀錄，保存不含完整逐字稿的可重現驗證結果。
- **ADR 範本**：ASR 架構決策紀錄的 Markdown 範本，記錄候選模型、證據參照、核准閘門、決策、風險與後續行動。
- **ASR-only 測試套件**：只涵蓋 ASR 領域套件、Colab 輸出 schema 與 ADR schema 的自動化測試，不呼叫 Chat API、Bedrock Agent、AWS、Flutter 或 Terraform。

## 需求

### 需求 1 — 建立獨立的 ASR 領域邊界

**使用者故事：** 身為後端整合者，我希望取得不依賴公開 Chat API 的 ASR 領域套件，使既有呼叫端能以一致介面取得 Transcript 或 Typed ASR Error。

#### 驗收條件

1. THE ASR 領域套件 SHALL 位於 `backend/src/shared/asr/`，並公開領域型別、Audio Canonicalizer、後端 ASR 設定、ASR Router 與 Provider Contract。
2. WHEN ASR 呼叫端呼叫 ASR 領域套件，THE ASR 領域套件 SHALL 只接收 Audio Bytes、Input Format、Language、Deadline、Cancellation Signal 與 Correlation Context 作為 ASR 輸入。
3. WHEN ASR 領域套件完成一次辨識處理，THE ASR 領域套件 SHALL 回傳一個非空白 Transcript 或一個 Typed ASR Error。
4. IF ASR 呼叫端提供空白 Audio Bytes、未支援的 Language 或缺少 Correlation Context，THEN THE ASR 領域套件 SHALL 回傳對應的 Typed ASR Error。
5. THE ASR 領域套件 SHALL 將公開 HTTP request schema、公開 HTTP response schema、公開 HTTP 錯誤格式、冪等、session reserve、session replay、Agent 工具、副作用、原子交易與 Agent/session persistence 排除在介面責任之外。

### 需求 2 — 定義可驗證的 Canonical Audio 邊界

**使用者故事：** 身為後端整合者，我希望 Audio Canonicalizer 產生固定的 Canonical Audio，使所有 Provider Contract 接收相同且可測試的音訊表示。

#### 驗收條件

1. WHEN Audio Canonicalizer 收到可解碼的 `wav` 或 `m4a` Audio Bytes，THE Audio Canonicalizer SHALL 產生 16,000 Hz、單聲道、16-bit signed little-endian PCM 的 Canonical Audio。
2. WHEN Audio Canonicalizer 收到時長小於或等於 60.000 秒的可解碼 Audio Bytes，THE Audio Canonicalizer SHALL 回傳 Canonical Audio 與以毫秒表示的精確音訊時長。
3. IF Audio Canonicalizer 收到損壞的 Audio Bytes、宣告與內容不符的 Input Format，THEN THE Audio Canonicalizer SHALL 回傳 `invalid_audio` Typed ASR Error。
4. IF Audio Canonicalizer 收到 Input Format 不是 `wav` 或 `m4a`，THEN THE Audio Canonicalizer SHALL 回傳 `unsupported_audio_format` Typed ASR Error。
5. IF Audio Canonicalizer 收到時長大於 60.000 秒的可解碼 Audio Bytes，THEN THE Audio Canonicalizer SHALL 回傳 `audio_duration_exceeded` Typed ASR Error。
6. THE Provider Contract SHALL 僅接收 Canonical Audio，不得接收未經 Audio Canonicalizer 產生的來源音訊格式。

### 需求 3 — 以後端設定路由 `zh-TW` 與 `hak`

**使用者故事：** 身為後端維運人員，我希望以後端 ASR 設定控制語言路由與核准狀態，使 ASR 呼叫端不需要知道模型或雲端服務選擇。

#### 驗收條件

1. THE 後端 ASR 設定 SHALL 為 `zh-TW` 與 `hak` 分別記錄 route、Provider 狀態、provider identifier 與相關模型中繼資料參照。
2. WHEN ASR Router 收到 `hak` Language 且 hak Mock Provider 為啟用狀態，THE ASR Router SHALL 將 Canonical Audio 路由至 hak Mock Provider。
3. WHEN hak Mock Provider 收到 Canonical Audio，THE hak Mock Provider SHALL 回傳決定性、非空白且不含 Audio Bytes 或完整逐字稿內容的安全測試 Transcript。
4. IF ASR Router 找不到 Language 對應的已啟用路由，THEN THE ASR Router SHALL 回傳 `unsupported_language` 或 `route_not_approved` Typed ASR Error。
5. THE 後端 ASR 設定 SHALL 將 Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 標示為僅限 Colab 驗證的模型中繼資料。
6. THE 後端 ASR 設定 SHALL 為 Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 保存模型識別、版本或 revision、授權、存取狀態、用途限制與核准狀態。
7. WHEN ASR Router 解析 Taiwan-Tongues-ASR-CE 或 FormoSpeech Whisper-v3 的 Production Invocation 路由，THE ASR Router SHALL 回傳 `route_not_approved` Typed ASR Error 且不得啟動模型傳輸。
8. THE 後端 ASR 設定 SHALL 將 Formo Prompt ID 限制為 `htia_sixian`、`htia_hailu`、`htia_dapu`、`htia_raoping`、`htia_zhaoan` 或 `htia_nansixian`。

### 需求 4 — 提供 fail-closed 的 AWS `zh-TW` Adapter Contract

**使用者故事：** 身為部署負責人，我希望在 AWS 能力完成核實前讓 `zh-TW` 路徑安全關閉，使未核准的服務、Region、權限或儲存假設不會進入正式流量。

#### 驗收條件

1. THE AWS `zh-TW` Adapter Contract SHALL 接收 Canonical Audio、`zh-TW` Language、Deadline、Cancellation Signal 與 Correlation Context，並回傳 Transcript 或 Typed ASR Error。
2. THE AWS `zh-TW` Adapter Contract SHALL 將 AWS 服務名稱、AWS Region 與服務輸入輸出模式維持為未指定狀態。
3. THE AWS Capability Gate SHALL 要求部署負責人逐項核實並核准 Region 的 `zh-TW` 支援、服務輸入與輸出模式、Canonical PCM 相容性、Timeout、Cancellation、IAM、S3 必要性、S3 結果處理與 S3 清理需求。
4. WHILE AWS Capability Gate 缺少任一核准項目，THE AWS `zh-TW` Adapter Contract SHALL 回傳 `route_not_approved` Typed ASR Error。
5. WHILE AWS Capability Gate 缺少任一核准項目，THE AWS `zh-TW` Adapter Contract SHALL 不呼叫任何 Transport。
6. THE AWS `zh-TW` Adapter Contract SHALL 只允許 Injected Fake Transport 用於 ASR-only 測試。
7. WHEN Deadline 到期或 Cancellation Signal 觸發，THE AWS `zh-TW` Adapter Contract SHALL 終止處理並回傳 `deadline_exceeded` 或 `cancelled` Typed ASR Error。
8. IF Injected Fake Transport 回傳空白文字、無效結果或未分類例外，THEN THE AWS `zh-TW` Adapter Contract SHALL 正規化結果為 `provider_invalid_response` 或 `provider_failure` Typed ASR Error。
9. THE AWS `zh-TW` Adapter Contract SHALL 不包含 AWS SDK 外呼、Terraform、IAM、S3、endpoint 或 Region 部署資源的實作要求。

### 需求 5 — 產生安全且可關聯的終態遙測

**使用者故事：** 身為後端維運人員，我希望每一次 ASR 處理都有可關聯的安全終態遙測，使路由與錯誤行為可診斷且不保留敏感音訊內容。

#### 驗收條件

1. WHEN ASR 領域套件完成 Transcript 或 Typed ASR Error，THE ASR 領域套件 SHALL 產生一筆 Safe Telemetry。
2. THE Safe Telemetry SHALL 只包含詞彙表列出的允許欄位。
3. THE Safe Telemetry SHALL 不包含 Audio Bytes、Canonical Audio 的樣本內容、完整 Transcript、HF Token、真實長者資料、Provider 原始 response 或 Formo Prompt ID。
4. WHEN Provider Contract 回傳成功、取消、逾期或失敗終態，THE ASR 領域套件 SHALL 將 `terminal_outcome`、`deadline_outcome`、`elapsed_ms` 與適用的 ASR Error Category 寫入 Safe Telemetry。
5. WHEN ASR 領域套件處理一組 Correlation Context，THE ASR 領域套件 SHALL 只產生一個終態結果與一筆對應的 Safe Telemetry。

### 需求 6 — 提供安全的 Taiwan-Tongues 與 FormoSpeech 免費 Colab 驗證包

**使用者故事：** 身為模型驗證人員，我希望可在免費 Colab 手動驗證兩個模型對合成 WAV 與 M4A 的處理，使模型資訊與前置條件可被保存而不暴露真實長者資料或機密。

#### 驗收條件

1. THE Colab 驗證包 SHALL 提供一個 Taiwan-Tongues-ASR-CE 驗證流程與一個 FormoSpeech Whisper-v3 驗證流程。
2. THE Taiwan-Tongues-ASR-CE 驗證流程 SHALL 記錄模型識別 `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0`、支援語言碼 `zh` 與 `hak`、授權標示 `other` 與僅限 Colab 驗證用途。
3. THE FormoSpeech Whisper-v3 驗證流程 SHALL 記錄模型識別 `formospeech/whisper-large-v3-taiwanese-hakka`、授權 `CC BY-NC 4.0`、gated-model 存取前置條件與僅限 Colab 驗證用途。
4. THE Colab 驗證包 SHALL 為每個驗證流程提供精確版本釘選的安裝清單、免費 GPU runtime 選擇步驟、WAV 輸入步驟、M4A 解碼步驟與合成測試音檔輸入步驟。
5. WHEN Colab 驗證流程收到可解碼的合成 WAV 或 M4A 測試音檔，THE Colab 驗證流程 SHALL 產生不含完整 Transcript 的結構化證據紀錄。
6. WHEN FormoSpeech Whisper-v3 驗證流程開始轉寫，THE FormoSpeech Whisper-v3 驗證流程 SHALL 只接受 Formo Prompt ID。
7. IF Colab GPU 無法使用、精確釘選依賴無法安裝、模型下載失敗、gated-model 存取未核准、HF Token 缺失、HF Token 無效或 M4A 解碼失敗，THEN THE 適用 Colab 驗證流程 SHALL 輸出失敗前置條件、失敗分類與可執行的重試步驟。
8. THE Colab 驗證包 SHALL 只接受合成測試音檔或已獲合法公開使用授權的測試音檔。
9. THE Colab 驗證包 SHALL 不將 HF Token 寫入 notebook、輸出、結構化證據紀錄或 ADR 範本。
10. THE Colab 驗證包 SHALL 不將完整 Transcript 寫入結構化證據紀錄或 ADR 範本。
11. THE Colab 驗證包 SHALL 不以 Taiwan-Tongues-ASR-CE 或 FormoSpeech Whisper-v3 執行 Production Invocation。

### 需求 7 — 定義證據與 ADR 的可驗證 Schema

**使用者故事：** 身為技術決策者，我希望取得一致且不含敏感內容的驗證證據與 ADR，使後續模型與部署決策具備可追溯依據。

#### 驗收條件

1. THE 結構化證據紀錄 SHALL 包含 `schema_version`、`run_id`、`recorded_at`、`model_id`、`model_revision`、`language`、`input_format`、`input_fixture_id`、`audio_duration_ms`、`runtime_kind`、`dependency_manifest_digest`、`outcome`、`failure_prerequisite`、`failure_category`、`transcript_present`、`transcript_character_count` 與 `evidence_redaction_version`。
2. WHEN 結構化證據紀錄的 `outcome` 為成功，THE 結構化證據紀錄 SHALL 將 `transcript_present` 設為 `true` 並將 `transcript_character_count` 設為大於零的整數。
3. WHEN 結構化證據紀錄的 `outcome` 為失敗，THE 結構化證據紀錄 SHALL 將 `failure_prerequisite` 與 `failure_category` 設為非空白值。
4. THE ADR 範本 SHALL 包含 `title`、`status`、`date`、`owners`、`scope`、`candidate_models`、`evidence_references`、`aws_capability_gate_status`、`decision`、`rationale`、`risks`、`non_goals` 與 `follow_up_actions` 區段。
5. WHEN ADR 範本引用結構化證據紀錄，THE ADR 範本 SHALL 只引用 `run_id`、`model_id`、`input_fixture_id`、`outcome` 與 `failure_category`。
6. THE ADR 範本 SHALL 將 Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 的 Production Invocation 標示為本期禁止的非目標。

### 需求 8 — 交付必做的 ASR-only 自動化測試

**使用者故事：** 身為後端維護者，我希望所有 ASR 領域規則都由可重複執行的 ASR-only 測試覆蓋，使未核准路由與敏感資料外洩可在不連接外部服務的情況下被發現。

#### 驗收條件

1. THE ASR-only 測試套件 SHALL 將 `pytest==8.3.5` 與 `hypothesis==6.122.3` 列為精確釘選的後端開發期依賴。
2. THE ASR-only 測試套件 SHALL 在 `asr-lambda/environment.yml` 建立並啟用的 `asr-model` conda 環境中，以 `python -m pip install -e ".[dev]"` 安裝開發期依賴，並以 `python -m pytest tests/asr -q` 執行 ASR-only 測試。
3. THE ASR-only 測試套件 SHALL 包含 ASR 領域型別、後端 ASR 設定、ASR Router、Provider Contract、hak Mock Provider、AWS `zh-TW` Adapter Contract、結構化證據紀錄 schema 與 ADR 範本 schema 的單元測試。
4. THE ASR-only 測試套件 SHALL 包含短 WAV、有效 M4A、損壞音訊、Input Format 與內容不符音訊、59.999 秒音訊、60.000 秒音訊與 60.001 秒音訊的 Canonical Audio fixture。
5. WHEN ASR-only 測試套件執行 Canonical Audio fixture，THE ASR-only 測試套件 SHALL 驗證短 WAV、有效 M4A、59.999 秒音訊與 60.000 秒音訊成功產生 Canonical Audio。
6. WHEN ASR-only 測試套件執行 Canonical Audio fixture，THE ASR-only 測試套件 SHALL 驗證損壞音訊與 Input Format 與內容不符音訊回傳 `invalid_audio` Typed ASR Error，並驗證 60.001 秒音訊回傳 `audio_duration_exceeded` Typed ASR Error。
7. THE ASR-only 測試套件 SHALL 包含路由與核准不變量的 property test，驗證任何缺少 AWS Capability Gate 核准項目的設定都不會選擇 Transport，且 Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 不會選擇 Production Invocation 路由。
8. THE ASR-only 測試套件 SHALL 包含 Adapter Contract 輸入、Deadline 與錯誤正規化的 property test，驗證 Injected Fake Transport 只收到 Canonical Audio 與允許的呼叫欄位，且所有逾期、取消、空白結果、無效結果與例外都收斂為定義的 Typed ASR Error。
9. THE ASR-only 測試套件 SHALL 包含終態結果與 Safe Telemetry 的 property test，驗證每組 Correlation Context 只有一個終態結果與一筆 Safe Telemetry，且 Safe Telemetry 不含未允許欄位或敏感內容。
10. THE ASR-only 測試套件 SHALL 包含 Formo Prompt ID allowlist 的 property test，驗證六個允許值以外的任何字串都不能通過設定或 Colab 驗證流程的驗證。
11. THE ASR-only 測試套件 SHALL 包含結構化證據紀錄 schema 與 ADR 範本 schema 的 property test，驗證有效資料可通過驗證，且缺少必填欄位、成功狀態缺少逐字稿存在資訊、失敗狀態缺少失敗資訊或包含禁止敏感欄位的資料無法通過驗證。
12. THE ASR-only 測試套件 SHALL 將所有單元測試、fixture 測試與 property test 視為必要測試，並在任一測試失敗時以非零結束狀態結束。
13. THE ASR-only 測試套件 SHALL 不建立 AWS 網路呼叫、真實模型呼叫、Chat API 整合測試、公開 API 整合測試、Bedrock Agent 測試、Flutter 測試或 Terraform 測試。

## 非目標與外部責任

- `POST /chat` 的 request schema、response schema、公開錯誤、冪等、session reserve 與 session replay 屬於既有 Chat API 外部責任；本期不得要求或修改相關元件。
- `docs/api.md` 是公開前後端契約；本期不得修改 `docs/api.md`。
- Bedrock Agent tools、routine/event side effects、原子交易、Agent persistence 與 session persistence 屬於既有對話與資料處理外部責任；本期不得要求或修改相關元件。
- 完整 Chat/API integration tests、端對端測試與部署驗證不屬於本期；本期不得要求相關測試。
- Flutter 程式、畫面、請求與設定不屬於本期；本期不得要求任何 Flutter 修改。
- 真實 AWS 服務或 Region 選擇、AWS SDK 外呼、Terraform、IAM、S3、endpoint 與部署資源不屬於本期；本期不得要求新增或修改相關元件。
- Taiwan-Tongues-ASR-CE 與 FormoSpeech Whisper-v3 僅可在安全免費 Colab 驗證流程使用；本期不得以兩個模型執行 Production Invocation。
- Colab 手動驗收不屬於 CI；ASR-only CI 僅執行需求 8 所列本機自動化測試。
