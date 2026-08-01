# ADR: ASR Remote-Only 架構

## 狀態

部分被取代（2026-08-01）

`Lambda 不載入模型`、單一 `ASR_CONFIG_JSON` 與 Formo prompt 固定於部署端仍有效；
「移除 AWS managed provider」及「SageMaker endpoints 只能全開／全關」已由
[`asr-managed-transcribe-routing.md`](asr-managed-transcribe-routing.md) 取代。

## 背景

ASR 初版曾同時考慮 Lambda 本機模型、SageMaker、自訂 AWS managed placeholder 與測試 mock。
Lambda 的冷啟動、記憶體與無 GPU 邊界使本機模型不可行，因此模型推論集中到遠端服務。

## 仍有效的決策

### 1. Remote-only 是執行位置邊界

Lambda 不可下載、載入或執行 ASR 模型；它只做音訊 canonicalization、設定驅動路由與
遠端 provider adapter。此邊界不排除具體、受控的 AWS managed service。

### 2. 唯一設定來源：`ASR_CONFIG_JSON`

移除分散的模型執行環境變數，改由單一 JSON 承載 routes、providers 與 model metadata；
AWS Region 是唯一例外。Parser 必須完整驗證且 fail closed。

### 3. Formo prompt 固定於部署端

Lambda 不傳 prompt ID。Formo 的方言 prompt 與 `FORMO_GENERATION_LANGUAGE=Chinese` 固定在
每個 SageMaker endpoint 的 container environment／部署設定中。

### 4. 模型核准與 endpoint 建立分離

SageMaker endpoint 存在不代表模型可接 production invocation。CE/Formo 必須逐模型通過
staging/runtime、授權、存取、配額與容量 gate；未核准時不得由 production route 外呼。

## 已被取代的決策

- 不再禁止 `aws_managed`。唯一允許的 managed provider 是
  `amazon_transcribe_zh_tw`，固定使用 Amazon Transcribe Streaming `zh-TW`。
- `asr_enable_endpoints` 只控制 SageMaker CE/Formo 資源；中文 Transcribe 不依賴此開關。
- 中文與客語有明確不同的主力／備援鏈，詳見現行 routing ADR。

## 保留的後果

- Lambda 部署包不含 torch、transformers、faster-whisper 或模型 artifact。
- 設定錯誤在 parse/composition 階段攔截。
- `asr-lambda/environment.yml` 只供 inference container 開發與 staging 驗證。
- `docs/api.md` 不暴露 provider 或 endpoint 細節。

## 相關文件

- 現行 routing 決策：[`asr-managed-transcribe-routing.md`](asr-managed-transcribe-routing.md)
- 架構入口：[`docs/asr/framework.md`](../asr/framework.md)
- 設定規格：[`docs/asr/config-schema.md`](../asr/config-schema.md)
