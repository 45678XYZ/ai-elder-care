# ADR: ASR Remote-Only 架構

## 狀態

已採用（2026-07）

## 背景

ASR 子系統原本設計允許四種 provider 路徑：本機模型推論（`local_model`）、
遠端 SageMaker 端點（`remote_model`）、AWS 代管 ASR 服務（`aws_managed`）、
與測試用 mock。隨著架構演進，確認了以下事實：

1. Lambda 環境不適合下載或執行 ASR 模型（冷啟動、記憶體、GPU 限制）
2. AWS 代管 ASR 服務（Transcribe 等）無具體使用場景，且與台灣客語需求不合
3. 模型推論應集中在 SageMaker Endpoint，以便獨立管理 GPU 資源與版本部署
4. 分散的環境變數（device、compute type、prompt ID、HF token）增加設定錯誤風險

## 決策

### 1. Remote-only：移除 Lambda 內模型推論

Lambda 不可下載、載入或執行 ASR 模型。模型只在 SageMaker Endpoint 執行。

**理由**：Lambda 的記憶體與執行時間限制不適合 GPU 推論；即使技術上可行，
也無法提供穩定的延遲與吞吐量。將模型集中在 SageMaker 可獨立調整資源。

### 2. Fail-closed：移除 AWS 代管 ASR placeholder

移除 `ProviderKind.AWS_MANAGED` 及其 9 項 capability gate。未完整設定或
未核准的路由回傳 `route_not_approved`，不可回落到任何替代方案。

**理由**：該路徑從未選定具體服務或 Region，capability gate 永遠不會完整；
保留它只增加程式碼複雜度與測試負擔，且給人「可以用」的錯誤印象。

### 3. 唯一設定來源：`ASR_CONFIG_JSON`

移除分散的環境變數（`ASR_LOCAL_DEVICE`、`ASR_LOCAL_COMPUTE_TYPE`、
`ASR_FORMO_PROMPT_ID`、`HF_TOKEN`），改由單一 JSON 環境變數承載所有設定。

**理由**：分散變數之間有隱含依賴（例如 prompt ID 只在 Formo provider 存在時有意義），
單一 JSON 可在 parser 層做完整性驗證，fail closed 而不是靜默使用部分設定。

### 4. Formo prompt 固定於部署端

Lambda 不傳送 prompt ID。Formo 的方言 prompt 固定在 SageMaker container
的環境變數或部署設定中。

**理由**：prompt 是部署決策而非 per-request 參數。Lambda 不需要知道
container 使用哪個腔調；這也避免了 prompt ID 在 request 流經的每一層被記錄。

### 5. Terraform `asr_enable_endpoints` 單一開關

啟用時必須同時提供 CE 和 Formo 的 image URI、model-data URL、artifact bucket
與精確的 Formo prompt ID。缺少任一參數時 validation 失敗。

**理由**：避免部分部署（只有一個 endpoint、沒有 artifact）造成的不一致狀態。
單一開關讓「全開」或「全關」成為唯二的合法狀態。

## 後果

### 正面

- Lambda 部署包更小（無 torch、transformers、faster-whisper 依賴）
- 冷啟動更快
- 設定錯誤在 parse 階段就能攔截
- 程式碼更少：移除了 `local_models.py`、`aws_zh_adapter.py` 與相關測試
- Terraform 的 fail-closed validation 防止不完整部署

### 負面

- 開發者無法在 Lambda 環境直接測試模型推論（需使用 SageMaker 或本機 conda 環境）
- 新增模型時除了 Python 程式碼，還需要建立 inference container

### 保留

- `asr-lambda/environment.yml` 與 `asr-model` Conda 環境保留為容器開發與驗證用途
- `provider_base.py`、`concurrency.py`、`ModelSlotPool`、`LazyModelHandle` 保留供遠端 provider 使用
- `docs/api.md` 不變（已隱藏內部 provider/endpoint 細節）

## 相關文件

- 遷移計畫：[`docs/asr/remote-only-migration-plan.md`](../asr/remote-only-migration-plan.md)
- 架構入口：[`docs/asr/framework.md`](../asr/framework.md)
- 設定規格：[`docs/asr/config-schema.md`](../asr/config-schema.md)
