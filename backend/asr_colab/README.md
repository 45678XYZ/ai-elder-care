# ASR Colab Validation — 共用契約

本文件定義 `taiwan_tongues_asr_ce/` 與 `formospeech_whisper_v3/` 兩個 Colab 驗證套件**必須共同遵守**的驗證流程規則、結構化證據輸出格式、匿名 fixture provenance 格式與安全禁止事項。

---

## 1. 目的與範圍

兩個套件**僅供人工 Colab validation**，不進入 CI、不建立 production endpoint、不呼叫本期的 AWS adapter、不做 CE/Formo Production Invocation，也不得呼叫任何實際 AWS service、SDK 或 network。

---

## 2. Notebook 執行先決條件

每個套件的 `validation.ipynb` **必須**：

1. **免費 GPU runtime 選擇** — 明示要求使用者於 Colab 選擇免費 GPU runtime；非 GPU 環境必須在 preflight 偵測並輸出失敗分類與 retry step。
2. **精確依賴安裝** — 使用 `requirements.lock` 中所列的精確 `package==version` 格式安裝所有依賴；不允許版本範圍、`>=`、`~=`、`*` 或 optional gate。
3. **WAV 輸入** — 支援 WAV 格式音訊作為主要輸入流程。
4. **M4A decode** — 支援 M4A 解碼流程；任何 M4A decode failure 必須輸出 `failure_prerequisite`、`failure_category` 與可執行的 retry step。
5. **前置條件檢查** — GPU 可用性、依賴安裝、模型下載、gated-model access、HF Token 有效性、decoder 可用性等前置條件一律在音訊處理前完成；任何失敗必須輸出分類與 actionable retry step。

---

## 3. `requirements.lock` 格式規則

- 每行恰為 `package==version`（精確固定版本）。
- 不允許版本範圍（`>=`、`<=`、`~=`、`!=`、`*`）。
- 不允許 extras、markers 或 optional test-only path。
- 檔案的 SHA-256 digest 必須寫入 evidence record 的 `dependency_manifest_digest` 欄位。

---

## 4. Fixture Provenance 規則

每個套件必須附帶 `fixture_provenance.json`，遵守 `fixture_provenance.schema.json` 定義的格式：

- **僅允許** synthetic（由專案建立）或具合法公開使用授權的音檔。
- 每筆 fixture 必須宣告：
  - `fixture_id`：匿名唯一識別碼（不含路徑、檔名或可識別個資）。
  - `source_type`：`"synthetic"` 或 `"publicly_licensed"`。
  - `license`：適用的 SPDX license identifier 或自訂授權描述。
  - `format`：`"wav"` 或 `"m4a"`。
  - `duration_ms`：音檔時長（毫秒）。
  - `description`：簡短用途說明。
- 不得存檔名、完整路徑、真實長者資料或可識別個人身份的資訊。

---

## 5. 結構化 Evidence 輸出規則

每次 Colab 驗證完成後，必須輸出符合 `evidence.schema.json` 的 JSON 或 JSON Lines evidence record。

### 5.1 Required Fields

每筆 evidence record **必須**包含以下所有欄位：

| 欄位 | 說明 |
|------|------|
| `schema_version` | Evidence schema 版本 |
| `run_id` | 本次驗證唯一識別碼 |
| `recorded_at` | ISO 8601 紀錄時間 |
| `model_id` | 模型識別 |
| `model_revision` | 模型版本/revision |
| `language` | 辨識語言碼 |
| `input_format` | 輸入音訊格式（`wav` / `m4a`） |
| `input_fixture_id` | 匿名 fixture ID（對應 fixture_provenance） |
| `audio_duration_ms` | 音訊時長（毫秒） |
| `runtime_kind` | 執行環境類型（例如 `colab_free_gpu`） |
| `dependency_manifest_digest` | `requirements.lock` 的 SHA-256 digest |
| `outcome` | `"success"` 或 `"failure"` |
| `failure_prerequisite` | 失敗時的前置條件描述（成功時為空字串） |
| `failure_category` | 失敗分類（成功時為空字串） |
| `transcript_present` | 是否成功產生逐字稿（boolean） |
| `transcript_character_count` | 逐字稿字元數（成功時 > 0 整數，失敗時為 0） |
| `evidence_redaction_version` | Redaction 版本號 |

### 5.2 Success/Failure 規則

- **`outcome = "success"`**：
  - `transcript_present` 必須為 `true`
  - `transcript_character_count` 必須為大於 0 的整數
  - record 不得包含 `transcript` 欄位

- **`outcome = "failure"`**：
  - `failure_prerequisite` 必須為非空白字串
  - `failure_category` 必須為非空白字串
  - 不得以空 transcript 冒充成功

### 5.3 `dependency_manifest_digest`

- 必須是對應套件 `requirements.lock` 檔案內容的 SHA-256 hex digest。
- 用於確保 evidence 可追溯至精確的依賴版本組合。

### 5.4 `evidence_redaction_version`

- 必須存在且為非空字串。
- 表示套用的去識別化規則版本，確保可追溯性。

---

## 6. 禁止欄位（Redacted/Forbidden Fields）

以下欄位**絕對不得**出現在 evidence record 中：

- `transcript` — 完整逐字稿
- `token` — 任何 token
- `hf_token` — HuggingFace Token
- `audio` — 音訊資料
- `audio_bytes` — 音訊位元組
- `pcm_samples` — PCM 樣本
- `prompt_id` — Prompt 識別碼
- `formo_prompt_id` — Formo Prompt 識別碼
- `raw_response` — 原始回應
- `raw_provider_response` — Provider 原始回應

---

## 7. 安全禁止事項

以下行為在兩個套件中**一律禁止**：

### 7.1 不得寫入 Notebook Source/Output、Evidence 或 Template

- HF Token（或任何 token）
- 完整 transcript（逐字稿全文）
- Audio bytes / PCM samples

### 7.2 不得執行

- Production Invocation（CE 或 Formo）
- 呼叫 task 5 的 AWS `zh-TW` adapter
- 任何實際 AWS service / SDK / network call
- 任何真實模型的 production 路由

### 7.3 HF Token 處理

- FormoSpeech 的 HF Token **僅**可從 Colab Secret 或短生命週期 runtime environment 讀取。
- 不得將 token 寫入 notebook source、cell output、requirements.lock、evidence 或 ADR。

---

## 8. ADR Evidence-Reference Projection

當 ADR 引用 evidence record 時，**僅允許**投影以下五個鍵：

- `run_id`
- `model_id`
- `input_fixture_id`
- `outcome`
- `failure_category`

任何其他欄位不得出現在 ADR evidence reference 中。

---

## 9. 目錄結構

```
backend/asr_colab/
├── README.md                          ← 本文件（共用契約）
├── evidence.schema.json               ← Evidence JSON Schema
├── fixture_provenance.schema.json     ← Fixture Provenance JSON Schema
├── taiwan_tongues_asr_ce/
│   ├── README.md
│   ├── validation.ipynb
│   ├── requirements.lock
│   ├── fixture_provenance.json
│   └── evidence.schema.json
└── formospeech_whisper_v3/
    ├── README.md
    ├── validation.ipynb
    ├── requirements.lock
    ├── fixture_provenance.json
    └── evidence.schema.json
```

---

## 10. 適用需求

本共用契約涵蓋以下需求：6.1, 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11, 7.1, 7.2, 7.3。
