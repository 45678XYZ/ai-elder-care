# ADR: Amazon Transcribe 與 SageMaker ASR 主備援路由

## 狀態

已採用（2026-08-01）

## 背景

繁體中文需要活動 AWS 環境可直接使用、具 Streaming 能力的主力 ASR；FormoSpeech
Whisper-v3 是客語模型，不應因 generation language 設為 `Chinese` 就承擔中文辨識。
Taiwan-Tongues CE 可涵蓋中／客語，但授權為 `other` 且尚未完成 production gate，適合保留為
共同備援。舊 ADR 曾因沒有具體使用場景而移除抽象 `aws_managed` placeholder；現在已選定
具體服務與固定能力，因此必須更新決策。

## 決策

### 1. Remote-only 允許受控 AWS managed service

Remote-only 的不變量是 Lambda 不下載、載入或執行模型。Amazon Transcribe Streaming 與
SageMaker 都是合法遠端執行位置。`aws_managed` 不作為任意擴充點：只允許精確 provider ID
`amazon_transcribe_zh_tw`。

### 2. 固定主力與同語言備援

- `zh-TW`：Amazon Transcribe Streaming → Taiwan-Tongues CE。
- `hak:<六腔>`：對應腔調 Formo endpoint → 同一個 Taiwan-Tongues CE endpoint。
- Formo capability 只登記 `hak`；`FORMO_GENERATION_LANGUAGE=Chinese` 只控制漢字輸出。

只有 `provider_unavailable`、`provider_failure`、`provider_invalid_response` 可 fallback。
輸入、語言、設定、核准、取消或逾期錯誤立即終止。

### 3. Transcribe 能力固定且 memory-only

Provider 內部固定 Streaming、`zh-TW`、PCM、16 kHz，只合併 final transcript。不得由設定覆寫
service/language/encoding/sample rate，不建立 batch job，也不使用 S3 暫存。

### 4. 自託管模型維持 fail closed

CE/Formo 只有在 `usage_restriction=production`、`approval_state=approved` 且五項 gate 全部
通過時才可處理 production invocation。證據必須來自指定 SageMaker instance 的
staging/runtime 品質、延遲與容量驗證。

### 5. 部署設定固定

- 六個 Formo endpoints 各固定 prompt 與 `FORMO_GENERATION_LANGUAGE=Chinese`。
- Formo instance mapping 與 CE `ml.g5.4xlarge` 依
  [`docs/asr/model-catalog.md`](../asr/model-catalog.md)；每端點一台，不建立 autoscaling。
- `asr_enable_endpoints` 只控制 SageMaker 資源；Transcribe route 不依賴 GPU 開關。

## 後果

### 正面

- 中文不再依賴未核准的自託管模型即可使用受控 Streaming service。
- 客語六腔仍由專用固定 prompt endpoint 主力處理，並共享同語言 CE 備援。
- Formo 的客語漢字輸出設定不會污染中文 capability。
- 所有音訊維持 memory-only，公開 API 不變。

### 代價與風險

- Chat Lambda 需要 `transcribe:StartStreamTranscription` 及 streaming client dependency。
- Transcribe、Formo 與 CE 有不同的服務錯誤與容量特性，必須以統一 typed errors 收斂。
- CE/Formo 尚未核准時，對應 fallback 不可用；客語 route 會 fail closed。

## 不在本 ADR 範圍

- 不核准 CE 或 Formo，不建立 endpoint，也不執行 Terraform apply。
- 不變更 `/chat` request/response 或 Flutter DTO。
- 不使用真實長者音訊、個資或健康資料做競賽驗證。
