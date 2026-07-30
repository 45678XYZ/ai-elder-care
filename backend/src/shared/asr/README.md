# ASR 領域套件 — 現行模組摘要

本檔是 `backend/src/shared/asr/` 的**現況**紀錄：模組職責、資料流、關鍵不變量與待辦。
改動這個套件時請一併更新本檔，避免與程式走鐘。

- 公開 API 契約以 [`docs/api.md`](../../../../docs/api.md) 為準，本套件不參與 HTTP 契約。
- 整體架構與資料模型見 [`docs/framework.md`](../../../../docs/framework.md)。
- 模型規格見 [`asr-lambda/docs/`](../../../../asr-lambda/docs/)。

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
        ├─ CeLocalProvider          CE，本 process 推論（faster-whisper，需 GPU）
        ├─ FormoLocalProvider       Formo，本 process 推論（transformers，僅客語）
        ├─ SageMakerAsrProvider     CE／Formo，呼叫自家託管端點
        └─ AwsZhAdapter             AWS 代管 ASR contract（服務未選定，僅 fake transport）
        │
        ▼
  Transcript | TypedAsrError  +  一筆 SafeTelemetryRecord
```

每個 provider 前面都夾著 `ModelSlotPool`（併發上限）與 `LazyModelHandle`（單次載入）。

---

## 3. 檔案職責

| 檔案 | 職責 | 狀態 |
|---|---|---|
| `__init__.py` | 公開表面：re-export 型別、facade、composition helper | 本次擴充 |
| `types.py` | 不可變領域型別：`InputFormat`、`Language`、`CanonicalAudio`、`Transcript`、`TypedAsrError`、`CorrelationContext`、`Deadline`、`CancellationSignal` | 既有，本次新增 `Deadline.remaining_seconds()` / `Deadline.after()` |
| `canonical_audio.py` | 音訊驗證、解碼與正規化；60 秒門檻 | 既有，未改 |
| `config.py` | 受控設定：route、provider、model metadata、AWS capability gate、**model production gate**、備援鏈、併發政策 | 本次大幅擴充 |
| `providers.py` | `AsrProvider` protocol、`TransportRequest`、**`AttemptRecord`**、**`ConcurrentAsrProvider`** protocol | 本次擴充 |
| `concurrency.py` | **新增**。`ModelSlotPool`（bounded 取號）、`LazyModelHandle`（thread-safe 單次載入 + 失敗冷卻） | 新增 |
| `provider_base.py` | **新增**。模型型 provider 的固定流程骨架（preflight → 取號 → handle → 推論 → postflight → 正規化） | 新增 |
| `local_models.py` | **新增**。`CeLocalProvider`、`FormoLocalProvider`（本 process 推論，需 GPU） | 新增 |
| `remote_endpoints.py` | **新增**。`SageMakerAsrProvider`（呼叫我們自己託管的推論端點） | 新增 |
| `failover.py` | **新增**。`FailoverChain`、`ChainOutcome`、可轉移／不可轉移錯誤分類 | 新增 |
| `hak_mock.py` | 客語 mock provider，固定文字 | 既有，未改 |
| `aws_zh_adapter.py` | AWS zh-TW adapter contract；服務／Region 仍未選定 | 既有，未改 |
| `router.py` | 固定 precedence 路由、provider 核准資格判定、備援鏈建構 | 本次重寫 |
| `facade.py` | 單一入口，協調各層並發出終態遙測 | 本次接線 |
| `telemetry.py` | allowlist 序列化、每請求一筆 | 本次新增 3 個觀測欄位 |
| `composition.py` | **新增**。production composition root：env → config → provider registry → facade，process 級快取 | 新增 |
| `evidence.py` | Colab evidence／ADR schema 驗證 | 既有，未改 |

---

## 4. 併發模型

**`AsrFacade.recognize` 可被多執行緒同時呼叫。** 支撐這個保證的設計：

- Facade、Router、各 provider 都不持有 per-request 可變狀態；每次呼叫自建
  `TerminalTelemetryEmitter`。
- 每個實體模型 provider 綁一個 `ModelSlotPool`（`BoundedSemaphore`）。
  `ProviderConfig.max_concurrent` 決定容量，預設 1，因為模型 handle 不可重入。
- 取號是 **bounded wait**：等不到就回報飽和（`admitted=False`），交給備援鏈決定
  溢流或放棄。不做無界排隊，才不會把呼叫端的 deadline 吃光。
- 模型以 `LazyModelHandle` double-checked locking 載入，保證每個 process 只載入
  一份；載入失敗進入冷卻期，避免每個請求都重試昂貴的下載。
- 所有等待上限都再被 `deadline.remaining_seconds()` 夾一次。

---

## 5. 備援鏈規則

`RouteConfig.provider_order = (primary, *fallback_chain)`，依序嘗試。

**會轉移到下一棒**（`DEFAULT_FAILOVER_CATEGORIES`）：

| 分類 | 情境 |
|---|---|
| `provider_unavailable` | 模型載不起來、gated 未授權、或**飽和**（`admitted=False`） |
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

## 6. 核准閘門（fail closed 的兩道鎖）

### AWS `zh-TW` Capability Gate（9 項，既有）

Region 支援、服務 I/O 模式、Canonical PCM 相容性、timeout、cancellation、IAM、
S3 必要性、S3 結果處理、S3 清理。任一未核准 → `route_not_approved`，且零外呼。
**服務與 Region 至今仍未選定。**

router 靠 `ProviderConfig.kind` 決定要驗哪一道閘門，不靠 provider 名稱猜：

| `ProviderKind` | 執行位置 | 需通過的閘門 |
|---|---|---|
| `mock` | 本 process，無模型 | 只需 `status == enabled` |
| `local_model` | 本 process，需 GPU | `enabled` + `metadata_ref` + 該 model 的 production gate 核准 |
| `remote_model` | 自家 SageMaker 端點 | 同 `local_model`，另需 `endpoint_name` |
| `aws_managed` | AWS 代管 ASR 服務 | `enabled` + AWS capability gate 九項全核准 |

`local_model` 與 `remote_model` 是**同一個模型換地方跑**，所以共用同一道 model
production gate。`aws_managed` 不同：那是 AWS 現成的 ASR 服務，模型不是我們的，
因此走另一道 capability gate。

資格判定排在取消與逾期**之前**：核准是管理決策，operator 排查一條被關閉的路由時
應該永遠看到 `route_not_approved`，而不是被當下的取消或逾期蓋掉。

### Model Production Gate（5 項，本次新增）

| 項目 | 意義 |
|---|---|
| `colab_validation_passed` | 已在 Colab 人工驗證跑出可用結果 |
| `license_cleared` | 授權允許實際用途。Formo 為 **CC BY-NC 4.0（限非商業）**，商業化後不得核准此項 |
| `access_granted` | gated model 存取權已取得（Formo 目前申請中） |
| `quota_cleared` | 推論額度足夠 |
| `runtime_capacity_verified` | GPU 記憶體與併發數已實測可承載 |

模型要能上線，必須同時滿足三個條件（`ModelMetadata.is_production_allowed`）：

```
usage_restriction == PRODUCTION
  AND approval_state == APPROVED
  AND production_gate.is_approved   # 上表 5 項全 True
```

**已變更的不變量**：原本 `parse_asr_config` 硬性拒絕「CE/Formo 標為 production」，
等於永久禁止上線。現改為「宣告 production 就必須同時具備 approved 狀態與逐項核准的
gate，否則 `ConfigParseError`」。`CE_MODEL_METADATA` 與 `FORMO_MODEL_METADATA` 的
gate 預設全為 False，因此**預設行為仍然是 fail closed**，需要有人明確核准才會開啟。

---

## 7. 遙測

每次 `recognize` 恰好一筆 `SafeTelemetryRecord`，鍵嚴格限於 allowlist（16 個）：

```
correlation_id, language, route, provider_id, input_format,
canonical_sample_rate_hz, canonical_channels, audio_duration_ms,
deadline_outcome, terminal_outcome, error_category, elapsed_ms, retryable,
attempt_count, queue_wait_ms, failover_occurred
```

最後三個是本次新增的併發／備援觀測欄位，全部是聚合數值或布林。沒有它們就分不出
「主力壞了但備援救回來」與「一次就成功」，也看不出流量是否已經在排隊。

`provider_id` 記的是**實際服務的 provider**（備援勝出時是備援者），不是設定中的主
provider。

絕不記錄：音訊 bytes、PCM samples、完整逐字稿、HF token、長者個資、
provider 原始回應、原始例外文字、Formo Prompt ID、endpoint／Region。

---

## 8. 組裝與設定（composition root）

呼叫端只需要 `get_asr_facade()`；它在 process 級快取，Lambda warm start 之間重用，
所以模型 handle 不必每次冷載。

```python
from src.shared.asr.composition import get_asr_facade

result = get_asr_facade().recognize(
    audio_bytes, input_format, language, deadline, cancellation, context
)
```

### 環境變數

| 變數 | 用途 | 預設 |
|---|---|---|
| `ASR_CONFIG_JSON` | 完整設定的 JSON 字串 | 無 → 用 `default_config()` |
| `ASR_LOCAL_DEVICE` | 實體模型推論裝置 | `cuda` |
| `ASR_LOCAL_COMPUTE_TYPE` | 推論精度 | `float16` |
| `ASR_FORMO_PROMPT_ID` | Formo 腔調（六個允許值之一） | 無 → 不建立 Formo provider |
| `HUGGING_FACE_HUB_TOKEN` / `HF_TOKEN` | gated model 存取 | 無 |
| `AWS_REGION` | 遠端端點所在區域（Lambda 本來就有） | 無 → 交給 boto3 解析 |

`ASR_CONFIG_JSON` 解析失敗一律拋 `ConfigParseError`，**不退回預設值**——否則
operator 打錯的設定會被靜默忽略，實際生效的東西與他以為的不同。

### 預設狀態的實際行為

| 語言 | 結果 |
|---|---|
| `hak` | `hak_mock` 回固定測試文字 |
| `zh-TW` | `route_not_approved`（AWS gate 不完整、CE 未核准） |

也就是說**預設只有客語 mock 能出結果**。要開通任何實體模型，必須在
`ASR_CONFIG_JSON` 明確填上 production gate 的五項核准。

### 選擇要用哪個模型

**在 CE 與 Formo 之間選、換主力／備援順序、調併發**：純設定，改 `ASR_CONFIG_JSON`
即可，不用碰程式碼。真正決定「用哪個模型」的是 `routes[語言].provider_identifier`
（主力）與 `fallback_chain`（備援，依序嘗試）。停用一個模型不需要改 route，只要
不核准它的 production gate，router 會自動跳過落到下一棒。

**加入一個全新的開源模型**：需要改程式，因為 `MODEL_PROVIDER_REGISTRY` 是
`model_id → 建構方式` 的明確登記表，不是「設定檔指定任意類別路徑」——後者等於
讓 JSON 可以載入任意程式碼，是安全風險。步驟：

1. 在 `local_models.py`（或 `remote_endpoints.py`）寫一個 `ModelProviderBase`
   子類別，實作 `_build_handle`／`_run_inference`／`_supports`。取號、逾時、
   取消、錯誤正規化全部由骨架處理。
2. 在 `composition.py` 的 `MODEL_PROVIDER_REGISTRY` 加一筆
   `ModelProviderRegistration`。
3. 在 `ASR_CONFIG_JSON` 的 `model_metadata` 填上這個模型並走完 production gate。

router 的核准判定、備援鏈、併發控制、遙測都不需要修改。未登記的 `model_id`
即使核准通過也不會建立實例——`build_provider_registry()` 會靜靜跳過它，這是刻意
的 fail closed，不是遺漏。

### 雙重防線

`build_provider_registry()` 只為「已核准」的模型建立實例；未核准的模型連物件都不
存在。這與 router 的資格判定重複，是刻意的：一層是決策，一層是根本沒有東西可以
被呼叫。

`ProviderKind.AWS_MANAGED` 的 transport 固定為 `None`，因為 AWS 服務與 Region
尚未選定。即使 capability gate 全數核准，這條路仍會回 `route_not_approved` 且
零外呼；真實 transport 必須等服務選定後另行接入。

### 基礎設施對應

`terraform/asr_models.tf` provision 兩個 SageMaker real-time endpoint（CE 主力、
Formo 客語備援）與 target-tracking autoscaling，供 `ProviderKind.REMOTE_MODEL`
呼叫。同樣預設關閉（`var.asr_enable_endpoints = false`）：模型未經驗證前，
程式層與基礎設施層都不開，也不產生 GPU 費用。

---

## 9. 執行環境與依賴

- `faster_whisper`、`transformers`、`torch`、`numpy` **一律延遲 import**。放模組頂層
  會讓 Lambda 冷啟載入數百 MB，也會讓不需要模型的單元測試無法執行。
- 本機開發用 `asr-lambda/environment.yml` 建立的 `asr-model` conda 環境。
- 測試：`python -m pytest tests/asr -q`；不連網（`conftest.py` 阻斷 socket）、
  不呼叫真實模型。

---

## 10. 測試對應

`backend/tests/asr/`，全部不連網、不呼叫真實模型。

| 測試檔 | 覆蓋 |
|---|---|
| `test_concurrency.py` | slot pool 容量上限、bounded wait 放棄、例外時歸還 slot、模型只載入一次、載入失敗冷卻 |
| `test_local_models.py` | 實體模型骨架：飽和不執行推論、取消／逾期優先於成功、例外不外洩、空白輸出、語言不符、拒絕非 Canonical Audio |
| `test_failover.py` | 錯誤分類完整二分、`route_not_approved` 永不被繞過、飽和溢流、取號預算分配、舊介面 provider 包裝 |
| `test_router_failover.py` | 核准後真的可上線、未核准不被呼叫、資格篩選（停用／無實例／無 metadata）、執行期轉移、公開讀取介面 |
| `test_composition.py` | 預設設定 fail closed、未核准模型無實例、Formo 缺腔調不建立、設定打錯直接失敗、facade process 級快取只組一次、stdout sink 只輸出 allowlist 欄位、註冊表恰為兩個已知模型、未登記 model_id 不建立實例、示範註冊第三個模型的最小改動 |
| `test_remote_endpoints.py` | 送出欄位集合封閉（只有 canonical PCM 與允許欄位）、暫時性錯誤→unavailable、逾時→deadline_exceeded、非 JSON／缺 text→invalid_response、例外不外洩、飽和不外呼 |
| `test_chat_asr_bridge.py` | 錯誤碼對映完整性、handler 不得自創 api.md 以外的錯誤碼、5xx 訊息不得內插例外文字、內部診斷只進日誌、60.000/60.001 秒邊界、text 路徑不受 ASR 設定影響、時間預算換算 |
| 既有 `test_*.py` | 型別、canonical audio、router、mock、AWS adapter、facade、telemetry、evidence、ADR |
| 既有 `test_property_*.py` | 五組 property test（每項 ≥ 100 iterations） |

---

## 11. 待辦與已知風險

| 項目 | 狀態 |
|---|---|
| 兩個模型的 Colab 人工驗證 | **未執行**。效能、實際可裝性、M4A decode 都還沒實測 |
| Formo gated access | 申請中 |
| Formo 腔調 prompt 的確切傳遞方式 | 目前採 HF 標準 `get_prompt_ids` + `generate(prompt_ids=)`，需對照模型卡確認（`local_models.py` 有 TODO） |
| AWS `zh-TW` 服務與 Region | 未選定，capability gate 預設不完整 |
| 推論容器 image 與模型 artifact | **不存在**。`terraform/asr_models.tf` 需要 `asr_ce_image_uri`／`asr_ce_model_data_url` 等值才能 apply；容器的輸入契約（raw PCM in、`{"text": ...}` out）目前只是約定，`remote_endpoints.py` 有 TODO |
| Terraform 未驗證 | 本機沒有 terraform CLI，`fmt`／`validate`／`plan` 皆未執行 |
| CE 輸出語言不保證 | 模型卡標明輸出文字不確定是哪種語言；本層只保證非空白 |
| 模型效能評測（WER／CER） | 不在現行範圍。現有 evidence schema 禁存逐字稿，無法事後算 WER |
| `POST /chat` 的 session 生命週期與冪等 | **未實作**。`chat.py` 只回傳形狀正確的 `session_id`（帶了沿用、沒帶新建），沒有 idle 門檻、turn 上限、closing/closed 狀態機，也沒有 `client_request_id` 冪等判定與 inflight reserve。`chat.py` 內有 TODO 標記 |
