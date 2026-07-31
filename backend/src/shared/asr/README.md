# ASR 領域套件 — 現行模組摘要

> **架構入口**：修改 ASR 之前請先閱讀 [`docs/asr/framework.md`](../../../../docs/asr/framework.md)。

本檔是 `backend/src/shared/asr/` 的**現況**紀錄：模組職責、資料流、關鍵不變量與待辦。
改動這個套件時請一併更新本檔，避免與程式走鐘。

- 公開 API 契約以 [`docs/api.md`](../../../../docs/api.md) 為準，本套件不參與 HTTP 契約。
- 整體架構與資料模型見 [`docs/framework.md`](../../../../docs/framework.md)。
- ASR 設定規格見 [`docs/asr/config-schema.md`](../../../../docs/asr/config-schema.md)。
- SageMaker 容器契約見 [`docs/asr/sagemaker-inference-contract.md`](../../../../docs/asr/sagemaker-inference-contract.md)。
- 安全與 PII 邊界見 [`docs/asr/security-and-pii.md`](../../../../docs/asr/security-and-pii.md)。
- 模型固定規格與核准狀態見
  [`docs/asr/model-catalog.md`](../../../../docs/asr/model-catalog.md)。

---

## 0. 呼叫端邊界

| 位置 | 角色 |
|---|---|
| `backend/src/shared/asr/` | ASR 領域套件（本檔描述的範圍），不認識 HTTP |
| `backend/src/shared/asr_http.py` | 領域錯誤 → `docs/api.md` 公開錯誤碼的對映，**刻意放在套件外** |
| `backend/src/handlers/chat.py` | 唯一後端呼叫端；負責 base64 解碼、時間預算、錯誤碼對映與日誌 |
| `app/lib/shared/services/api_client.dart` | App 端；送出 base64 音訊、依 `error.code` 分支、處理重送的冪等鍵 |

---

## 1. 這個套件負責什麼

把「呼叫端交來的一段音訊」轉成「一段非空白逐字稿，或一個具型別的錯誤」，並且：

- 統一音訊表示（Canonical Audio），讓所有推論後端吃同一種格式。
- 由後端設定決定用哪個模型，呼叫端無法指定 provider、模型或服務。
- 未經核准的路徑一律 fail closed，不做任何外呼。
- 可同時服務多個請求，且單一實體模型不會被併發呼叫壓垮。
- 主 provider 故障或飽和時自動改用備援 provider。
- 每次請求只留一筆去識別化的終態遙測。

**不負責**：HTTP request/response、認證授權、session、冪等、資料庫、對話工作流、TTS。
這些是呼叫端（`handlers/chat.py`）的責任。

**架構禁則（remote-only）**：Lambda 不可下載、載入或執行 ASR 模型。模型只在
SageMaker Endpoint 執行。Lambda 只負責將正規化音訊傳送到 SageMaker、驗證回應、
並將文字交給既有聊天流程。

---

## 2. 資料流

```
audio_bytes + input_format + language + deadline + cancellation + context
        │
        ▼
  AsrFacade.recognize          ← 單一入口；建立 telemetry emitter
        │
        ├─ input gate           空音訊 / 無效 context → invalid_audio
        ▼
  canonical_audio.canonicalize  WAV/M4A 驗證與解碼 → mono / 16 kHz / PCM S16LE
        │                       格式不符 → unsupported_audio_format
        │                       損毀或宣告不符 → invalid_audio
        │                       > 60.000 s → audio_duration_exceeded
        ▼
  AsrRouter.route_detailed      語言 → route → provider 資格 → 取消 → 逾期
        │
        ▼
  FailoverChain.run             依序嘗試 provider，記錄每次 attempt
        │
        ├─ HakMockProvider          決定性測試文字，無模型/網路
        ├─ SageMakerAsrProvider     CE／Formo，呼叫自家託管 SageMaker 端點
        │
        ▼
  Transcript | TypedAsrError  +  一筆 SafeTelemetryRecord
```

每個遠端 provider 前面都夾著 `ModelSlotPool`（併發上限）與 `LazyModelHandle`（連線建立）。

---

## 3. 檔案職責

| 檔案 | 職責 |
|---|---|
| `__init__.py` | 公開表面：re-export 型別、facade、composition helper |
| `types.py` | 不可變領域型別：`InputFormat`、`Language`、`CanonicalAudio`、`Transcript`、`TypedAsrError`、`CorrelationContext`、`Deadline`、`CancellationSignal` |
| `canonical_audio.py` | 音訊驗證、解碼與正規化；60 秒門檻 |
| `config.py` | 受控設定：route、provider、model metadata、**model production gate**、備援鏈、併發政策 |
| `providers.py` | `AsrProvider` protocol、**`AttemptRecord`**、**`ConcurrentAsrProvider`** protocol |
| `concurrency.py` | `ModelSlotPool`（bounded 取號）、`LazyModelHandle`（thread-safe 單次載入 + 失敗冷卻） |
| `provider_base.py` | 模型型 provider 的固定流程骨架（preflight → 取號 → handle → 推論 → postflight → 正規化） |
| `remote_endpoints.py` | `SageMakerAsrProvider`（呼叫自家託管的 SageMaker 推論端點） |
| `failover.py` | `FailoverChain`、`ChainOutcome`、可轉移／不可轉移錯誤分類 |
| `hak_mock.py` | 客語 mock provider，固定文字 |
| `router.py` | 固定 precedence 路由、provider 核准資格判定、備援鏈建構 |
| `facade.py` | 單一入口，協調各層並發出終態遙測 |
| `telemetry.py` | allowlist 序列化、每請求一筆 |
| `composition.py` | production composition root：env → config → provider registry → facade，process 級快取 |
| `evidence.py` | Colab evidence／ADR schema 驗證 |

---

## 4. 併發模型

**`AsrFacade.recognize` 可被多執行緒同時呼叫。** 支撐這個保證的設計：

- Facade、Router、各 provider 都不持有 per-request 可變狀態；每次呼叫自建
  `TerminalTelemetryEmitter`。
- 每個實體模型 provider 綁一個 `ModelSlotPool`（`BoundedSemaphore`）。
  `ProviderConfig.max_concurrent` 決定容量，預設 1，因為模型 handle 不可重入。
- 取號是 **bounded wait**：等不到就回報飽和（`admitted=False`），交給備援鏈決定
  溢流或放棄。不做無界排隊，才不會把呼叫端的 deadline 吃光。
- 遠端 provider 以 `LazyModelHandle` double-checked locking 建立 boto3 client，
  保證每個 process 只建立一份；建立失敗進入冷卻期，避免每個請求都重試。
- 所有等待上限都再被 `deadline.remaining_seconds()` 夾一次。

---

## 5. 備援鏈規則

`RouteConfig.provider_order = (primary, *fallback_chain)`，依序嘗試。

**會轉移到下一棒**（`DEFAULT_FAILOVER_CATEGORIES`）：

| 分類 | 情境 |
|---|---|
| `provider_unavailable` | endpoint 不可用、gated 未授權、或**飽和**（`admitted=False`） |
| `provider_failure` | 推論拋出未分類例外 |
| `provider_invalid_response` | 回傳非文字或空白 |

**不會轉移，立即終止**（`NON_FAILOVER_CATEGORIES`）：

| 分類 | 為什麼不轉移 |
|---|---|
| `cancelled` / `deadline_exceeded` | 呼叫端已不要結果，再試是浪費 |
| `invalid_audio` / `unsupported_audio_format` / `audio_duration_exceeded` | 音訊本身的問題，換誰都一樣 |
| `unsupported_language` | 不在契約內 |
| `route_not_approved` | **這是管理決策而非故障**。用備援繞過未核准路由等於讓 fail-closed 失效 |

其他規則：

- 每一棒之前重新檢查取消與逾期。
- 非最後一棒的取號上限是 `spill_wait_ms`（快速溢流）；最後一棒才允許用完剩餘
  deadline，因為它後面沒有人可以接。
- provider 直接拋例外（而非回傳 `TypedAsrError`）時收斂為 `provider_failure`，
  單一 provider 的實作瑕疵不會中斷整條鏈。
- `ChainOutcome` 回報 `attempts`、`served_provider_id`、`failover_occurred`、
  `total_queue_wait_ms`，供遙測記錄。

---

## 6. 核准閘門（fail closed）

### Model Production Gate（5 項）

router 靠 `ProviderConfig.kind` 決定要驗哪一道閘門：

| `ProviderKind` | 執行位置 | 需通過的閘門 |
|---|---|---|
| `mock` | 本 process，無模型/網路 | 只需 `status == enabled` |
| `remote_model` | SageMaker 端點 | `enabled` + `metadata_ref` + production gate 核准 + `endpoint_name` |

| 項目 | 意義 |
|---|---|
| `colab_validation_passed` | 已在 Colab 人工驗證跑出可用結果 |
| `license_cleared` | 授權允許實際用途。Formo 為 **CC BY-NC 4.0（限非商業）**，商業化後不得核准此項 |
| `access_granted` | gated model 存取權已取得 |
| `quota_cleared` | 推論額度足夠 |
| `runtime_capacity_verified` | 執行環境已實測可承載 |

模型要能上線，必須同時滿足三個條件（`ModelMetadata.is_production_allowed`）：

```
usage_restriction == PRODUCTION
  AND approval_state == APPROVED
  AND production_gate.is_approved   # 上表 5 項全 True
```

資格判定排在取消與逾期**之前**：核准是管理決策，operator 排查一條被關閉的路由時
應該永遠看到 `route_not_approved`，而不是被當下的取消或逾期蓋掉。

---

## 7. 遙測

每次 `recognize` 恰好一筆 `SafeTelemetryRecord`，鍵嚴格限於 allowlist（16 個）：

```
correlation_id, language, route, provider_id, input_format,
canonical_sample_rate_hz, canonical_channels, audio_duration_ms,
deadline_outcome, terminal_outcome, error_category, elapsed_ms, retryable,
attempt_count, queue_wait_ms, failover_occurred
```

`provider_id` 記的是**實際服務的 provider**（備援勝出時是備援者），不是設定中的主
provider。

絕不記錄：音訊 bytes、PCM samples、完整逐字稿、HF token、長者個資、
provider 原始回應、原始例外文字、Formo Prompt ID、endpoint／Region。

---

## 8. 組裝與設定（composition root）

呼叫端只需要 `get_asr_facade()`；它在 process 級快取，Lambda warm start 之間重用。

```python
from src.shared.asr.composition import get_asr_facade

result = get_asr_facade().recognize(
    audio_bytes, input_format, language, deadline, cancellation, context
)
```

### 環境變數

| 變數 | 用途 | 預設 |
|---|---|---|
| `ASR_CONFIG_JSON` | 完整設定的 JSON 字串（唯一的 ASR 設定來源） | 無 → 用 `default_config()` |
| `AWS_REGION` | 遠端端點所在區域（Lambda 本來就有） | 無 → 交給 boto3 解析 |

`ASR_CONFIG_JSON` 解析失敗一律拋 `ConfigParseError`，**不退回預設值**——否則
operator 打錯的設定會被靜默忽略，實際生效的東西與他以為的不同。

### 預設狀態的實際行為

| 語言 | 結果 |
|---|---|
| `hak` | `hak_mock` 回固定測試文字 |
| `zh-TW` | `route_not_approved`（CE 未核准） |

也就是說**預設只有客語 mock 能出結果**。要開通任何實體模型，必須在
`ASR_CONFIG_JSON` 明確填上 production gate 的五項核准。

這個預設只供測試與明確的本機開發使用。Terraform 部署永遠注入明確設定；endpoint
關閉時兩條 production route 都停用，因此 `zh-TW`／`hak` 都回 `route_not_approved`，
不會在 production 使用 `hak_mock`。

### 選擇要用哪個模型

**在 CE 與 Formo 之間選、換主力／備援順序、調併發**：純設定，改 `ASR_CONFIG_JSON`
即可，不用碰程式碼。停用一個模型不需要改 route，只要不核准它的 production gate，
router 會自動跳過落到下一棒。

**加入一個全新的開源模型**：需要改程式，因為 `MODEL_PROVIDER_REGISTRY` 是
`model_id → 建構方式` 的明確登記表，不是「設定檔指定任意類別路徑」。步驟：

1. 在 `remote_endpoints.py` 寫一個 `ModelProviderBase` 子類別，實作
   `_build_handle`／`_run_inference`／`_supports`。
2. 在 `composition.py` 的 `MODEL_PROVIDER_REGISTRY` 加一筆
   `ModelProviderRegistration`。
3. 在 `ASR_CONFIG_JSON` 的 `model_metadata` 填上這個模型並走完 production gate。

未登記的 `model_id` 即使核准通過也不會建立實例——`build_provider_registry()` 會
靜靜跳過它，這是刻意的 fail closed。

### 雙重防線

`build_provider_registry()` 只為「已核准」的模型建立實例；未核准的模型連物件都不
存在。這與 router 的資格判定重複，是刻意的：一層是決策，一層是根本沒有東西可以
被呼叫。

### 基礎設施對應

`terraform/asr_models.tf` provision 兩個 SageMaker real-time endpoint（CE 主力、
Formo 客語備援）與 target-tracking autoscaling，供 `ProviderKind.REMOTE_MODEL`
呼叫。預設關閉（`var.asr_enable_endpoints = false`）：模型未經驗證前，
程式層與基礎設施層都不開，也不產生 GPU 費用。

---

## 9. 執行環境與依賴

- 執行期依賴：`boto3`、`pydantic`、`numpy`、`soundfile`、`av`（見
  `pyproject.toml` 與部署用 `requirements.txt`）。後三者只負責音訊解碼、
  downmix 與 resample，不包含模型推論。
- 開發期工具：`pytest`、`hypothesis`（`pip install -e ".[dev]"`）。
- 容器開發環境：`asr-lambda/environment.yml` 建立的 `asr-model` conda 環境。
- 測試：`python -m pytest tests/asr -q`；不連網（`conftest.py` 阻斷 socket）、
  不呼叫真實模型。

---

## 10. 測試對應

`backend/tests/asr/`，全部不連網、不呼叫真實模型。

| 測試檔 | 覆蓋 |
|---|---|
| `test_concurrency.py` | slot pool 容量上限、bounded wait 放棄、例外時歸還 slot、handle 只載入一次、載入失敗冷卻 |
| `test_failover.py` | 錯誤分類完整二分、`route_not_approved` 永不被繞過、飽和溢流、取號預算分配、舊介面 provider 包裝 |
| `test_router_failover.py` | 核准後真的可上線、未核准不被呼叫、資格篩選、執行期轉移、公開讀取介面 |
| `test_composition.py` | 預設設定 fail closed、未核准模型無實例、設定打錯直接失敗、facade process 級快取只組一次、stdout sink 只輸出 allowlist 欄位、註冊表恰為兩個已知模型 |
| `test_remote_endpoints.py` | 送出欄位集合封閉、暫時性錯誤→unavailable、逾時→deadline_exceeded、非 JSON／缺 text→invalid_response、例外不外洩、飽和不外呼 |
| `test_remote_only_routing.py` | remote-only 路由行為（20 項含 property tests） |
| `test_terraform_config_contract.py` | Terraform JSON 與 Python parser 的契約一致 |
| `test_chat_asr_bridge.py` | 錯誤碼對映完整性、handler 不得自創 api.md 以外的錯誤碼、遠端成功／路由拒絕／endpoint 錯誤 |
| 既有 `test_*.py` | 型別、canonical audio、router、mock、facade、telemetry、evidence、ADR |
| 既有 `test_property_*.py` | 四組 property test（每項 ≥ 100 iterations） |

---

## 11. 待辦與已知風險

| 項目 | 狀態 |
|---|---|
| 兩個模型的 Colab 人工驗證 | **未執行**。效能、實際可裝性、M4A decode 都還沒實測 |
| Formo gated access | 申請中 |
| Formo 腔調 prompt 固定在 SageMaker container | 部署前須選定腔調並寫入 container 設定 |
| 推論容器 image 與模型 artifact | **不存在**。`terraform/asr_models.tf` 需要 image URI 與 model-data URL 才能 apply |
| CE 輸出語言不保證 | 模型卡標明輸出文字不確定是哪種語言；本層只保證非空白 |
| 模型效能評測（WER／CER） | 不在現行範圍 |
| `POST /chat` 的 session 生命週期與冪等 | **未實作**。`chat.py` 內有 TODO 標記 |
