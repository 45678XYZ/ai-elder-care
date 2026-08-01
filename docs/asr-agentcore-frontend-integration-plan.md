# main 分支 ASR／Bedrock Agent／Frontend 相容性整併計畫

## 文件狀態

- 狀態：本文的 Bedrock Agents Classic 段落已過時。`main` 已於 `784e50b` 把對話大腦遷到
  AgentCore Runtime，本計畫「暫不遷移 AgentCore」的前提不再成立；ASR/TTS 的部分仍有效。
- 實作基線：`main@9a9e972376337583f61295db24bf276e7ce66c98`
- ASR 參考：`feature/asr-lambda@bcb035a43332f085405d6b5388d4eafd925b8866`
- Merge commit：`feature/asr-lambda@6a01712`（main → ASR branch）
- Frontend 參考：`feature/app-screens-and-reminders@1e90c0c346a1a5a713a2dac6f672384e8b8efb91`
- 公開 API 契約：[`docs/api.md`](api.md)
- 系統架構：[`docs/framework.md`](framework.md)
- ASR 架構：[`docs/asr/framework.md`](asr/framework.md)

本計畫依 2026-07-31 的 repo 狀態制定。原始規劃將兩個 feature branch 只視為需求、
設計與測試案例來源；執行時依使用者後續授權，將 `main` merge 進既有
`feature/asr-lambda`，衝突一律以 `main` 為骨架，再選擇性接回已驗證的 ASR 行為。
`feature/app-screens-and-reminders` 仍僅供參考，未直接整併。

> 執行註記：使用者後續明確改為要求將 `main` merge 進既有 `feature/asr-lambda`。
> 2026-07-31 已依 main-first 原則完成 merge；這項授權取代 Phase 0 的新分支方式，
> 不改變 ASR remote-only、production fail-closed 與 frontend 僅供參考的決策。

## 1. 已確認決策

1. **以 `main` 為唯一實作基線**：保留 `main` 的 session、turn 冪等、batch extraction、
   routines、events、summaries、stats 與 Bedrock Agent 流程。
2. **分支處理方式已更新**：依使用者授權，`main` 已 merge 進既有
   `feature/asr-lambda`；`feature/app-screens-and-reminders` 不 merge／rebase，僅參考
   其中的 request shape、錄音格式與 UX 行為。衝突內容仍以 `main` 為準。
3. **ASR 先達成安全可整併，不宣稱可正式辨識**：遠端模型未完成驗證與人工核准前，
   production 路由一律 fail closed。
4. **Frontend 不在本次實作範圍**：只用 frontend 分支的 request shape、session 重試、
   AAC-LC M4A 錄音與 UX 行為作相容性驗收依據。
5. **暫時沿用 `main` 的 Amazon Bedrock Agents 實作**：本次不遷移 AgentCore Runtime。
   文件與 ADR 必須準確標示它是 Bedrock Agents Classic 暫行架構，並保留後續遷移項目。

## 2. 目標與非目標

### 2.1 目標

- 把 remote-only ASR 能力安全地重作到最新 `main`。
- 保留 `main` 的 `/chat` turn 三態、request lease、session reserve／close 與 terminal
  replay 語意。
- 讓文字路徑不受 ASR 設定或模型狀態影響。
- 讓未核准的 audio 路徑穩定回傳既有 `docs/api.md` 錯誤，不呼叫遠端、不使用假逐字稿、
  不留下部分業務副作用。
- 修正 `main` 中 Bedrock Agent tools 的 scope、記憶與 transaction 邊界，使 ASR 加入後
  不放大既有資料一致性風險。
- 建立可重現的 Lambda 打包、Terraform validation 與完整測試門檻。
- 保持未啟用 ASR endpoint 時不建立 GPU 資源、不產生 endpoint 費用。

### 2.2 非目標

- 不執行真實模型 production rollout。
- 不執行 `terraform apply` 或 `terraform destroy`。
- 不核准 CE／Formo 模型，不偽造 staging/runtime evidence、授權、quota 或容量證據。
- 不建立或實作 SageMaker inference container image；本次只保留已定義的 container I/O
  契約與 gated IaC。
- 不整併 Flutter 畫面、Cognito SDK、照護者綁定 endpoint 或 frontend branch。
- 不把 Bedrock Agents Classic 遷移成 AgentCore Runtime／Memory／Gateway。
- 不變更 `POST /chat` 的公開 request／response shape。

## 3. 不可破壞的不變量

### 3.1 `/chat` 與資料一致性

- `client_request_id` 冪等判定必須先於 session 選擇、建立或 reserve。
- 只有全新 request 才可 reserve session inflight 名額。
- ASR、Agent、TTS 任一步失敗，都要把已 reserve turn 收成穩定 terminal `failed`，
  並移除 inflight reservation。
- completed turn replay 必須回原 `conversation_id`、`session_id` 與業務結果，只重新簽發
  audio URL。
- write tools 產生的 routine/event mutation 必須和 turn completion 同一個 DynamoDB
  transaction 提交；不可由 Agent tools Lambda 提前留下獨立副作用。

### 3.2 ASR

- Lambda 不下載、載入或執行 ASR 模型。
- 不加入 `torch`、`transformers`、`faster-whisper` 或其他模型推論依賴。
- `ASR_CONFIG_JSON` 是 Chat Lambda 唯一的 ASR 設定來源。
- 中文固定 Amazon Transcribe Streaming → CE；客語六腔固定對應 Formo endpoint → CE。
- 只有 provider unavailable/failure/invalid response 可進同語言備援；未核准 provider 不外呼。
- Formo prompt 與 `FORMO_GENERATION_LANGUAGE=Chinese` 固定在 SageMaker container 部署設定；
  Lambda request 不傳，且 Formo capability 仍只允許 `hak`。
- 音訊 bytes、逐字稿、HF token、長者個資與 provider 原始回應不得進入日誌或錯誤訊息。
- `docs/api.md` 的 ASR request／response 契約維持不變。

### 3.3 Production fail-closed

`default_config()` 的 `hak_mock` 只允許單元測試與明確的本機開發設定使用。Terraform
部署的 Chat Lambda 即使 `asr_enable_endpoints=false`，仍要注入一份明確
`ASR_CONFIG_JSON`：中文啟用受控 Transcribe，SageMaker providers 停用，且 production
不啟用 mock，避免客語音訊被轉成固定測試文字。

預期狀態如下：

| 環境 | `zh-TW` audio | `hak` audio | 外呼 | GPU endpoint |
|---|---|---|---|---|
| 單元測試／明確 local mock | fail closed 或測試指定 provider | `hak_mock` | 否 | 否 |
| Production、endpoint 未啟用 | Transcribe | `route_not_approved` | 僅 Transcribe | 否 |
| Endpoint 已建但模型未核准 | Transcribe；CE 不可作備援 | `route_not_approved` | Transcribe；模型不得導流 | 有，但不得導流 |
| 模型證據與五項 gate 全通過 | Transcribe → CE | Formo → CE | 受控 Transcribe／核准 endpoint | 有 |

## 4. 現況缺口

| 區域 | `main` 現況 | 目標 |
|---|---|---|
| Chat ASR | 直接讀 `SAGEMAKER_CE_ENDPOINT_NAME`；未設定時回固定假逐字稿 | 改走 ASR facade、唯一設定、remote-only、fail closed |
| 音訊格式 | 原始 M4A/WAV 直接傳 endpoint，且 metadata 與新契約不同 | Lambda 正規化為 mono/16 kHz/PCM S16LE |
| ASR 錯誤 | 使用未列於 API 的 `TRANSCRIPTION_FAILED` 等 code | 只回 `INVALID_PARAMETER`、`AUDIO_TOO_LONG`、`INTERNAL_ERROR` |
| Lambda 打包 | Module A 引用未建立的 `terraform/build/backend.zip` | Terraform 可重現打包，Chat 使用獨立 audio runtime requirements |
| Agent 記憶 | `sessionId=elder_id`，未傳 `memoryId`，未在 App session close 時結束 agent session | stable `memoryId=elder_id`、`sessionId=<App session_id>`，close 後安全結束 |
| Agent tools scope | 模型可自行提供 `elder_id`，tools 直接信任 | 只使用後端已驗證的 elder scope |
| Agent tools atomicity | tools Lambda 先寫 DB，Chat 之後才 commit turn | write action return-control → Chat 驗證、stage、同 transaction commit |
| `routines_updated` | 依未開啟的 response trace 字串猜測 | 依實際成功提交的 staged actions 計算 |
| Frontend | `main` 仍多為 placeholder | 本次只建立契約相容性，不納入畫面程式 |
| 真實模型 | 無 image、artifact、實測與 production approval | 維持未核准，列為後續 gate |

## 5. 實作階段

### Phase 0：建立乾淨基線與可重現驗證

1. 取得執行授權後，從最新 `main` 建立新的 integration branch，不沿用
   `feature/asr-lambda`：

   ```powershell
   git checkout main
   git pull --ff-only
   git checkout -b feature/main-asr-integration
   ```

2. 記錄 baseline commit，確認工作樹乾淨。
3. 建立隔離 Python venv，依 `backend/README.md` 安裝 dev dependencies。
4. 執行 `python -m pytest -q`，baseline 必須維持目前的 `650 passed` 或更新後的合理數量。
5. 在 CI／具工具的環境補跑 `terraform fmt -check -recursive`、`terraform validate`、
   `flutter analyze` 與 `flutter test`；本機缺工具不得視為通過。

完成條件：

- 分支只從最新 `main` 建立。
- baseline 測試結果被記錄。
- 未帶入兩個 feature branch 的 merge commit。

### Phase 1：選擇性重作 remote-only ASR 領域層

以 `feature/asr-lambda` 為參考，逐檔 review 後在新分支重作下列能力：

- `backend/src/shared/asr/`
  - canonical audio
  - config parser 與 production gate
  - provider protocol／registry
  - router／failover
  - Amazon Transcribe Streaming 與 SageMaker remote providers
  - concurrency／deadline／cancellation
  - telemetry allowlist
  - facade／composition root
- `backend/src/shared/asr_http.py`
- `backend/tests/asr/`
- synthetic audio fixtures
- `docs/asr/` 與 ASR ADR

必須在 port 時重新確認：

- 移除任何 local model；AWS managed 只允許精確 ID `amazon_transcribe_zh_tw`。
- `ProviderKind` 只保留 `mock`、`aws_managed`、`remote_model`。
- model registry 只允許明確登記的模型。
- 未核准 provider 不建立實例，也不能外呼。
- 錯誤與 telemetry 不含原始音訊、逐字稿或例外文字。
- 修正文件中已被 `main` 解決的「session 尚未實作」舊狀態。

完成條件：

- ASR domain 不依賴 HTTP、DB、session 或 Agent。
- `python -m pytest tests/asr -q` 全過。
- 搜尋不到 local-model 路徑，以及 `torch`、`transformers`、`faster-whisper` 的
  production dependency；`AWS_MANAGED` 只可出現在受控 Transcribe 路徑。

### Phase 2：把 ASR 接入 `main` 的 Chat turn state machine

`backend/src/handlers/chat.py` 必須以 `main` 版本為骨架，禁止整檔採用 ASR branch 版本。

修改方向：

1. 保留 `ChatRequest`、`sessions`、`turns`、request hash、lease、reserve、commit、
   replay 與 close race 行為。
2. 移除：
   - `SAGEMAKER_CE_ENDPOINT_NAME`
   - `CE_ASR_API_URL`
   - `get_sagemaker_runtime()`
   - handler 內的直接 `invoke_endpoint`
   - endpoint 未設定時的固定 mock transcript
3. 在 `run_turn()` 的 audio 分支呼叫 `get_asr_facade().recognize(...)`。
4. 將 Lambda 剩餘時間扣除 Agent／TTS／S3 所需尾端預算後傳入 ASR deadline。
5. `ConfigParseError` 與 `TypedAsrError` 必須先經 `asr_http.py` 安全化，再轉成
   `TurnFailure`；不可把內部 message 存進 terminal turn。
6. 4xx audio 問題與 5xx route/provider 問題都必須收斂 terminal turn，確保 session
   inflight 不會卡住。
7. text path 不組裝 ASR facade；即使 `ASR_CONFIG_JSON` 壞掉，text chat 仍可工作。
8. 只在終端狀態記錄 correlation ID、分類與去識別化統計；不得記錄 transcript。

必要測試：

- text path 不讀 ASR config。
- invalid base64 在 reserve 前回 400。
- 真實時長超過 60 秒時，已 reserve turn 會安全 terminalize failed。
- route 未核准時不建立 SageMaker client、不外呼。
- provider failure 回公開 `INTERNAL_ERROR`，不回 endpoint 名稱或 exception。
- 同一 request replay 相同 ASR failure，不重新外呼。
- ASR 成功後 Agent/TTS/commit 流程與 main 相同。
- commit 失敗時不回 200。
- session closing/closed 與 audio turn race 不破壞 main 規則。

### Phase 3：修正 main Bedrock Agent 邊界，但不遷移 AgentCore

本階段沿用 `bedrock-agent-runtime.invoke_agent` 與 `aws_bedrockagent_agent`，但要把文件
中的「AgentCore」名稱改成準確的「Bedrock Agents Classic 暫行實作」。AgentCore Runtime
遷移另列後續工作。

#### 3.1 Session 與 memory

- 將 Agent 呼叫簽名改為：

  ```text
  invoke_agent_brain(elder_id, app_session_id, transcript)
  ```

- `memoryId` 使用穩定的 `elder_id`。
- `sessionId` 使用目前 turn 實際 reserve 的 App `session_id`，不可再用 `elder_id`。
- client close 與 idle close 成功後，以同一 agent session 發出 `endSession=true`。
- Agent end-session 失敗不得 reopen 已 closed 的 DynamoDB session；記錄安全化 metric，
  並由 Bedrock idle TTL 作最終保底。

#### 3.2 Tool scope

- `elder_id` 只能來自已通過 Cognito 授權與 session ownership 檢查的後端 context。
- 模型輸出的 `elder_id` 不得作為授權依據；最好從 write tool schema 移除。
- 若為相容性暫時保留參數，Chat 必須覆寫或驗證它與 trusted elder scope 完全一致。
- 增加跨 elder prompt-injection 測試。

#### 3.3 Write tool atomicity

將 action group 拆成：

- read tools：可立即查詢，不改業務資料。
- write tools：設定 `action_group_executor.custom_control = "RETURN_CONTROL"`。

write tools 流程：

1. Agent 回傳 `invocationInputs` 與 `invocationId`。
2. Chat 驗證 function allowlist、參數、trusted elder scope、每 turn action 上限。
3. Chat 只建立 deterministic staged action，不先寫 DB。
4. 將 validation/result 透過同一 `invocationId` 回送 Agent，使其完成文字回覆。
5. TTS 成功後，由 `turns.commit()` 在同一 DynamoDB transaction 寫入：
   - completed turn
   - routine create/update/deactivate/complete
   - safety event
   - session inflight release 與 turn append
6. `routines_updated`／`rt_labels` 由真正提交的 staged actions 計算，不解析 trace 字串。

AWS 的 return-control 行為參考：

- [ActionGroupExecutor API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_ActionGroupExecutor.html)
- [Return control 流程](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)

完成條件：

- TTS 或 final commit 失敗時沒有提前留下 routine/event。
- 同一 `client_request_id` 重送不會重複 tool 副作用。
- 模型無法讀寫其他 elder 的資料。
- `routines_updated` 與實際 transaction 結果一致。

### Phase 4：修正 Python 依賴與 Lambda 打包

目前 ASR canonicalization 需要 native audio runtime，但 `main` 的 Module A 打包與
requirements 不足。建議：

1. 建立 Chat 專用 requirements，例如 `backend/requirements-chat.txt`：
   - `pydantic`
   - `numpy`
   - `soundfile`
   - `av`
   - 其餘實際需要且可進 Lambda 的小型 runtime dependency
2. 不加入 `librosa`；沿用可測試的輕量 resample fallback，避免帶入 SciPy／Numba 體積。
3. `backend/pyproject.toml` 與部署 requirements 必須同步，避免本機測得到、Lambda import
   失敗。
4. 將 main 的 Module A raw `aws_lambda_function` 重構為可重現的 Lambda module 打包：
   - zip 內保留 `src/` package
   - handler 使用 `src.handlers.chat.handler`
   - Chat 使用專用 requirements
   - tools／elders 不被迫打包 ASR native libraries
5. 在 Linux／Lambda Python 3.11 相容環境建立 artifact，不採用 Windows wheel。
6. 增加部署包 smoke test：

   ```powershell
   python -c "from src.handlers import chat; from src.shared.asr import canonical_audio"
   ```

7. 檢查 zip 壓縮與解壓大小、cold start、512 MB memory 與 `/tmp` 使用；音訊仍不得落地。

完成條件：

- 不再引用不存在的手工 `terraform/build/backend.zip`。
- Chat artifact 可在 Lambda 相容環境 import `numpy`、`soundfile`、`av`。
- 非 Chat Lambda 不含 ASR native dependencies。

### Phase 5：加入預設關閉的 ASR Terraform

選擇性重作：

- `terraform/asr_models.tf`
- `terraform/asr_lambda_config.tf`
- ASR variables、outputs、IAM attachment

要求：

1. `asr_enable_endpoints=false` 為預設。
2. false 時：
   - SageMaker model、endpoint config、endpoint、autoscaling 全部 count 為 0。
   - Chat 仍收到中文 Transcribe 啟用、SageMaker providers 停用的明確 `ASR_CONFIG_JSON`。
   - production 不啟用 `hak_mock`。
3. true 時必須同時提供：
   - CE image URI
   - CE model-data URL
   - Formo image URI
   - Formo model-data URL
   - artifact bucket
4. 六個 Formo prompt 與 `FORMO_GENERATION_LANGUAGE=Chinese` 只注入各 SageMaker container
   environment，不注入 Chat Lambda。
5. endpoint 建立與 route production approval 是兩道獨立 gate；只有資源存在仍不得導流。
6. Chat IAM 只允許 `transcribe:StartStreamTranscription` 與
   `sagemaker:InvokeEndpoint` 到七個明確 endpoint ARN；移除 main 的 `Resource="*"`。
7. `ASR_CONFIG_JSON` 透過 Chat Lambda environment 注入，不建立個別 endpoint env。
8. 只執行：

   ```powershell
   terraform fmt -check -recursive
   terraform init -backend=false
   terraform validate
   terraform plan -var="asr_enable_endpoints=false"
   ```

完成條件：

- disabled plan 顯示零個 ASR GPU endpoint。
- 缺任何啟用前置參數時 validation 失敗。
- 未核准模型即使 endpoint 存在也不會被 provider registry 建立。
- 不執行 apply。

### Phase 6：文件與 API 契約同步

同一批行為修改中更新：

- `docs/framework.md`
- `backend/README.md`
- `docs/pii.md`
- `docs/asr/framework.md`
- `docs/asr/config-schema.md`
- `docs/asr/security-and-pii.md`
- `docs/asr/sagemaker-inference-contract.md`
- `backend/src/shared/asr/README.md`
- `docs/adr/asr-remote-only.md`
- 新增「Bedrock Agents Classic 暫行」ADR
- 根目錄 `README.md` 文件索引與結構樹

`docs/api.md` 的 audio shape 與 ASR provider 細節保持不變；若只修正實作使用了契約未定義
的錯誤碼，應改程式回既有 code，而不是擴張 API：

| 內部失敗 | 公開 code |
|---|---|
| invalid/corrupt audio | `INVALID_PARAMETER` |
| audio > 60 秒 | `AUDIO_TOO_LONG` |
| route 未核准、設定錯誤、provider failure、deadline | `INTERNAL_ERROR` |
| Agent、TTS、未知 server failure | `INTERNAL_ERROR` |

文件也要移除已失效的待辦，例如「session 生命週期未實作」，並把真實未完成事項保留：

- 模型人工驗證
- image/artifact
- Formo access/license/prompt
- WER/CER 與真實延遲
- AgentCore Runtime 遷移
- Flutter/Cognito 與 caregiver binding

### Phase 7：完整驗證

#### Backend

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m pytest -q
python -m pytest tests/asr -q
python -m pytest tests/asr/test_chat_asr_bridge.py -q
```

額外建立整合測試：

- text chat regression
- audio request → ASR success → Agent → TTS → atomic commit
- audio request → fail closed → terminal replay
- reserve／close race
- write tool return-control 多輪 orchestration
- tool scope 越權
- TTS failure after staged action
- transaction failure after staged action
- production config 禁止 mock
- Terraform JSON 與 Python parser contract

#### Terraform

```powershell
cd terraform
terraform fmt -check -recursive
terraform validate
terraform plan -var="asr_enable_endpoints=false"
```

#### Frontend 契約相容性

不合併 frontend 程式，但以參考分支行為驗證 backend：

- `POST /chat` 接受 AAC-LC M4A base64 與 `format=m4a`。
- 第一輪可省略 `session_id`，後續回傳值可直接帶回。
- 同一次輸入重送沿用 `client_request_id`。
- close 409 可安全重送。
- `reply_audio_url=null` 時 frontend 可退回裝置端 TTS。
- `routines_updated=true` 只在 transaction 已提交時回傳。
- 45 秒 client timeout 內若不確定結果，必須用原 request ID replay。

具 Flutter SDK 的 CI 再執行：

```powershell
cd app
dart format --output=none --set-exit-if-changed .
flutter analyze
flutter test
```

## 6. 建議 PR 與 commit 切分

所有 PR 都從最新 `main` 建立，不 merge 參考分支。

### PR 1：main Chat／Agent 前置修正

- 修正未定義錯誤碼與 5xx exception leakage。
- 導入 trusted elder scope。
- write tools 改 return-control 與 staged transaction。
- 修正 Agent `memoryId`／`sessionId`。
- 補 main regression tests 與暫行 ADR。

建議 commits：

- `fix(chat): align terminal errors with api contract`
- `fix(agent): enforce trusted elder scope`
- `refactor(agent): stage write tools for atomic commit`
- `docs(agent): record classic agent interim architecture`

### PR 2：remote-only ASR domain 與 Chat 整合

- 重作 ASR domain、HTTP bridge、fixtures 與 tests。
- 以 main Chat state machine 接入。
- 修正 runtime dependencies 與 Module A 打包。
- 同步 ASR 文件。

建議 commits：

- `feat(asr): add remote-only recognition domain`
- `fix(chat): integrate fail-closed asr with turn lifecycle`
- `chore(backend): package chat audio dependencies`
- `test(asr): cover main chat integration`
- `docs(asr): align remote-only integration guidance`

### PR 3：ASR gated infrastructure

- 加入預設關閉的 SageMaker resources。
- 注入中文 Transcribe enabled、SageMaker providers disabled 的 production `ASR_CONFIG_JSON`。
- 加入 scoped IAM、validation、outputs 與 Terraform tests。

建議 commits：

- `feat(terraform): add gated asr endpoints`
- `test(terraform): verify asr config contract`
- `docs(asr): document deployment gates`

每個 commit 只 stage 該 concern 的檔案，不使用 `git add -A`。未取得使用者明確授權前，
不得 commit、push、merge 或 rebase。

## 7. 主要風險與處置

| 風險 | 等級 | 處置 |
|---|---|---|
| Production 誤用 `hak_mock` | P0 | Terraform 永遠注入明確 production config；測試斷言中文只用受控 Transcribe、客語不會回固定假逐字稿 |
| Agent write tool 提前寫 DB | P0 | RETURN_CONTROL + staged actions + `turns.commit()` transaction |
| 模型提供其他 `elder_id` | P0 | trusted scope 取代模型參數，加入 prompt-injection 測試 |
| M4A native dependency 在 Lambda import 失敗 | P0 | Linux artifact build、Chat 專用 requirements、deploy-package smoke test |
| 未核准模型被導流 | P0 | build-time 與 router 雙重 gate，route_not_approved 不 fallback |
| API contract 出現未定義錯誤碼 | P1 | 統一映射到既有 code，加入 source/contract test |
| ASR + Agent + TTS 超過 Lambda/API timeout | P1 | 明確 latency budget、保留尾端時間、測試 deadline 與 replay |
| Bedrock Agents Classic 生命週期風險 | P1 | 暫行 ADR、避免宣稱 AgentCore、另開 migration plan |
| 真實模型／container 不存在 | P1 | endpoints 預設 false，不把 IaC 完成誤認為可上線 |
| Frontend branch 新增 backend 尚無的 caregiver APIs | P2 | 本次不合併 frontend；未來另案實作 API、PII 與授權 |
| 文件舊 TODO 與現況矛盾 | P2 | 行為變更同 PR 更新權威文件與 README |

Bedrock Agents Classic 與 AgentCore 是不同服務。本計畫暫時沿用 main，但需保留遷移風險：

- [Bedrock Agents Classic memory](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-memory.html)
- [AgentCore Runtime invocation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html)

## 8. Definition of Done

必須全部成立才可稱為「安全整併完成」：

- [x] 依使用者授權將最新 `main` merge 進 ASR branch，衝突以 `main` 為骨架；frontend
      branch 未整併。
- [x] main 原有 backend tests 全過。
- [x] ASR tests 與 Chat bridge tests 全過。
- [x] text chat 行為與 main 相容。
- [ ] SageMaker-disabled config 下，`zh-TW` 只外呼受控 Transcribe，`hak` fail closed，且不使用
      固定假逐字稿。
- [x] 相同 `client_request_id` replay 不重做 ASR 或 tool side effects。
- [x] ASR failure 會 terminalize turn 並釋放 inflight。
- [ ] write tools、turn completion 與 session 更新同 transaction 提交。
- [ ] 模型無法跨 elder 存取資料。
- [ ] 所有公開 error code 均存在於 `docs/api.md`。
- [ ] 日誌不含音訊、逐字稿、長者個資、endpoint 名稱或原始 provider response。
- [ ] Lambda artifact 可在 Python 3.11 Lambda 相容環境 import audio dependencies。
- [ ] `terraform validate` 通過。
- [x] 補充的 OpenTofu compatibility validate 通過，且 lock file 遷移已還原。
- [ ] disabled Terraform plan 不建立 ASR endpoint 或 autoscaling 資源。
- [x] 沒有執行 Terraform／OpenTofu apply。
- [ ] README、framework、ASR 文件、PII 與 ADR 已同步。
- [ ] PR diff 沒有 frontend 畫面、Cognito 或 caregiver binding 的非預期修改。

## 9. 後續另案

以下不阻擋本次 fail-closed 整併，但不得被宣稱已完成：

1. 建置並驗證 CE／Formo SageMaker inference containers。
2. 在指定 SageMaker instance 執行 staging/runtime、WER/CER、合成 M4A、延遲、quota 與容量測試。
3. 完成授權審查；Formo 的非商業授權不得在商業情境誤核准。
4. 逐模型完成 production approval ADR，再開啟 route。
5. 從 Bedrock Agents Classic 遷移 AgentCore Runtime／Memory／Gateway。
6. 另案整併 frontend branch、Cognito SDK、caregiver binding API 與 PII consent。
7. 在真實 Android 裝置驗證 AAC-LC M4A、靜音斷句、timeout replay 與免手持迴圈。

## 10. 執行紀錄

本章保存可重現的執行結果與 ASR 整併判斷；它取代僅存在對話或終端捲動紀錄中的摘要。
除非另有註明，命令均於 2026-07-31 在 `feature/asr-lambda` 執行。所有變更目前皆未 push，
本次也沒有取得 commit 授權。

### 10.1 main merge

- 已將 `main@9a9e972376337583f61295db24bf276e7ce66c98` merge 進
  `feature/asr-lambda@bcb035a43332f085405d6b5388d4eafd925b8866`。
- merge commit 為 `6a0171255e1a5acdc61171762d49924614bae711`。
- Chat handler、依賴、Terraform 與文件衝突均以 `main` 為骨架，再接回 remote-only ASR、
  fail-closed routing 與對應測試；未把 frontend branch 合入。
- 未執行 push、Terraform/OpenTofu plan、apply 或 destroy。

### 10.2 Backend 與 ASR 測試

| 驗證範圍 | 命令 | 結果 |
|---|---|---|
| Backend 全套 | `cd backend; python -m pytest -q` | `1034 passed in 66.49s` |
| ASR 全套 | `cd backend; python -m pytest tests/asr -q` | `384 passed in 18.10s` |
| Chat／ASR bridge | `cd backend; python -m pytest tests/test_chat_handler.py tests/asr/test_chat_audio_integration.py tests/asr/test_chat_asr_failure_integration.py -q` | `60 passed in 13.61s` |
| Terraform ASR config contract | `cd backend; python -m pytest tests/asr/test_terraform_config_contract.py -q` | `9 passed in 0.16s` |

這些結果證明目前的 orchestration、錯誤映射、冪等與設定契約通過自動測試；不代表真實
CE／Formo 模型的辨識準確率、延遲、容量、授權或 production readiness 已驗證。

### 10.3 Terraform 格式與 OpenTofu 相容性檢查

本專案仍以 **Terraform** 為 IaC 工具，不改用 OpenTofu。因本機找不到 Terraform CLI，
僅以既有的 OpenTofu `1.12.5` 做一次相容性檢查：

| 驗證 | 結果 |
|---|---|
| 初次 `tofu fmt -check -recursive` | exit code `3`；列出 `api_gateway.tf`、`asr_lambda_config.tf`、`bedrock_agent.tf`、`eventbridge.tf`、`lambda.tf` |
| 格式修正後 `tofu fmt -check -recursive` | 通過 |
| `tofu init -backend=false -input=false` | 通過；只初始化本機 provider/module，不連接 backend |
| `tofu validate` | 通過，另有 12 個既存 deprecated warnings；代表性項目為 DynamoDB `range_key` 應遷移至 `key_schema` |

格式修正與 lock file 處理刻意分開：

- 上述五個 `.tf` 檔的 formatter-only 修正保留為獨立、尚未 commit 的工作樹變更；不得與
  ASR 邏輯或 lock file 遷移混成同一 commit。
- OpenTofu init 曾把 provider source 寫成 `registry.opentofu.org`；已完整還原
  `.terraform.lock.hcl`，目前沒有 OpenTofu registry 遷移差異，也不應提交該遷移。
- OpenTofu validate 只能作為補充相容性證據，不能取代合併前的原生 `terraform fmt`、
  `terraform validate` 與 disabled plan。

### 10.4 ASR 必要整併細節

#### 執行邊界與設定

- 架構維持 remote-only：Lambda 只做音訊 canonicalization、路由判斷與呼叫已部署的遠端
  endpoint，不下載、載入或執行模型。
- production 設定只從 `ASR_CONFIG_JSON` 取得；設定缺失或解析失敗必須 fail closed。
  純文字 Chat 不得建立 ASR client，也不得因 ASR 設定失效。
- `default_config()` 的 `hak_mock` 只供單元測試或明確本機開發。Terraform 在 endpoints
  關閉時仍注入 production config：`zh-TW` 使用受控 Transcribe，`hak` 回
  `route_not_approved`；不得外呼 GPU endpoint，也不得回傳固定假逐字稿。
- Lambda dependency 僅包含 canonicalization 所需的 `numpy`、`soundfile` 與 `av`；不得
  加入 `torch`、`transformers` 或 `faster-whisper` 等模型 runtime。

#### 音訊與 Chat turn lifecycle

- API 接受 base64 WAV／M4A；進入 provider 前統一轉成 mono、16 kHz、PCM S16LE WAV，
  canonical duration 必須不超過 60 秒。音訊只在記憶體處理，不寫 S3、DynamoDB 或暫存檔。
- handler 目前另有解碼前 5 MiB 防護；高 bitrate／多聲道但未滿 60 秒的合法輸入仍可能
  先被拒絕，需在真實裝置測試後確認是否調整，不能把此 guard 當成精確時長檢查。
- `client_request_id` 的冪等判定必須早於 session 選擇／建立／reserve；audio request 只可
  透過 ASR facade，correlation ID 必須為 UUID，且 provider 呼叫要保留 Lambda 尾端預算。
- ASR 失敗必須 terminalize turn 並釋放 inflight；相同 request replay 只能回既有 terminal
  結果，不得再次呼叫 ASR、Agent 或產生 tool side effects。
- 對外錯誤維持既有 API 契約：無效或損壞音訊映射 `INVALID_PARAMETER`，超過 60 秒映射
  `AUDIO_TOO_LONG`，路由未核准、設定錯誤、provider 失敗或 deadline 映射
  `INTERNAL_ERROR`；不得洩漏內部例外或 endpoint 資訊。

#### 路由、PII 與基礎設施 gate

- 自託管 provider 只有在 route enabled、metadata 完整、approval state 合法、五項 production
  gates 全數通過且 endpoint 存在時才可呼叫。Managed provider 只允許固定 Transcribe ID；
  `route_not_approved` 不得 fallback 到 mock 或另一個未核准模型。
- 日誌採固定 16 欄 allowlist；不得記錄原始／canonical 音訊、逐字稿、provider 原始回應、
  `elder_id`、endpoint 名稱或 exception message。
- ASR endpoint Terraform flag 預設為 `false`，所以預設為零 GPU；中文 Transcribe 不受此
  開關影響。開啟前必須備妥 CE／Formo image、model artifact、bucket 與部署設定，並通過
  授權與人工 approval；IAM 權限須限定到 Transcribe action 與核准 endpoints。此次未執行
  任何 plan 或 apply。

#### 尚未解決、不可誤稱完成

- `main` 的 raw Lambda artifact 仍引用 `terraform/build/backend.zip`；Python 3.11 Linux native
  wheels、`av`／`soundfile` 載入與 deploy-package smoke test 尚未完成，這是實際部署 blocker。
- 真實 CE／Formo image 與 model artifact、指定 instance staging/runtime／合成 M4A、
  WER／CER、延遲、quota、容量與授權均未驗證；Formo access 已取得，但非商業授權在
  商業情境不得核准。
- Bedrock Agents Classic 仍有 `sessionId`／`memoryId`、trusted elder scope、write tool 原子性
  等風險；本次通過的 ASR bridge tests 不能替代 Phase 3 的安全修正。
- Frontend branch 尚未整併；Cognito SDK、caregiver binding APIs、AAC-LC 真機錄音與 timeout
  replay 仍需另案驗證。
