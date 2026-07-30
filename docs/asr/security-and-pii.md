# ASR 安全邊界與 PII 規範

本文件定義 ASR 子系統的安全邊界、PII 禁則與日誌限制。
通用 PII 政策見 [`docs/pii.md`](../pii.md)；本文件只涵蓋 ASR 特有規則。

相關文件：
- 架構入口：[`docs/asr/framework.md`](./framework.md)
- SageMaker 容器契約：[`docs/asr/sagemaker-inference-contract.md`](./sagemaker-inference-contract.md)
- 遙測實作：`backend/src/shared/asr/telemetry.py`

---

## 1. 不可記錄的資料

以下資料**絕不**可出現在日誌（Lambda CloudWatch Logs、SageMaker container logs、
stdout telemetry、錯誤訊息）中：

| 類別 | 具體內容 |
|---|---|
| 音訊內容 | raw PCM bytes、WAV/M4A 原始 bytes、base64 編碼音訊 |
| 辨識結果 | 逐字稿文字（`Transcript.text`）、provider 回傳的 `text` 欄位 |
| 認證憑證 | HuggingFace token、AWS session credentials |
| 長者個資 | elder_id、姓名、聯絡資訊、健康資訊 |
| Provider 原始回應 | SageMaker InvokeEndpoint 回傳的完整 JSON body |
| 原始例外訊息 | 可能包含路徑、token、音訊片段的 traceback |
| Formo Prompt ID | 部署設定不可在 request-level 日誌出現 |
| Endpoint 名稱/Region | 基礎設施細節不對外揭露 |

---

## 2. 遙測 Allowlist

每次 `AsrFacade.recognize` 恰好一筆 `SafeTelemetryRecord`，
鍵嚴格限於以下 16 個去識別化欄位：

```
correlation_id          # UUID，與 elder_id 無關
language                # "zh-TW" | "hak"
route                   # 路由名稱
provider_id             # 實際服務的 provider ID
input_format            # "wav" | "m4a" | "pcm"
canonical_sample_rate_hz # 16000
canonical_channels      # 1
audio_duration_ms       # 毫秒數
deadline_outcome        # "within" | "exceeded"
terminal_outcome        # "success" | "error"
error_category          # enum value 或 null
elapsed_ms              # 整體耗時
retryable               # boolean
attempt_count           # 嘗試次數
queue_wait_ms           # 取號等待總時間
failover_occurred       # boolean
```

### Allowlist 規則

- 新增欄位必須修改 `TELEMETRY_ALLOWLIST_KEYS` 並更新本文件
- 欄位值必須是聚合數值、enum 或 UUID，不可是自由文字
- `provider_id` 記實際服務的 provider（備援勝出時是備援者）
- `correlation_id` 由呼叫端提供，與 elder_id 分離

---

## 3. 音訊生命週期

```
App base64 → Chat Lambda (decode) → memory only → ASR Facade → SageMaker
                                         │
                                         └─ 完成後 GC 回收
```

### 限制

- 音訊**只在記憶體中存在**
- 不可寫入 Lambda `/tmp`
- 不可存入 DynamoDB
- 不可存入 S3（ASR 音訊不是 TTS 音檔）
- Lambda 函數結束後記憶體即釋放

### PCM 傳送到 SageMaker

- Body 是 raw PCM bytes，不是 base64
- 傳輸走 AWS SDK 內部 HTTPS，不經公開網路
- SageMaker endpoint 收到後在 container 記憶體中推論，不保存

---

## 4. SageMaker Endpoint Container 的日誌限制

Container CloudWatch Logs **不得**包含：

- 音訊 bytes 或 PCM samples
- 辨識結果文字（逐字稿）
- HF token
- 長者個資
- Lambda 傳入的 CustomAttributes 原始值
- Provider 原始回應的完整 JSON

Container **允許**記錄：

- 請求的 audio duration（秒數或 byte count）
- 推論延遲（毫秒）
- 成功/失敗狀態碼
- 模型版本識別
- 錯誤分類（不含原始例外訊息）

---

## 5. 錯誤訊息安全化

### Lambda 端（`provider_base.py`）

- 未分類例外一律替換為固定安全訊息（如 `"Inference failed in provider 'ce_remote'."`）
- 原始 traceback 不在 `TypedAsrError.message` 出現
- 例外文字可進 Lambda CloudWatch Logs 的結構化日誌（`logger.exception`），
  但日誌層級須為 ERROR 且不含音訊或逐字稿

### Chat Handler 端（`handlers/chat.py`）

- 5xx 錯誤訊息不得內插例外文字
- 內部診斷只進日誌，不回傳給 App

---

## 6. 與 `docs/pii.md` 的關係

`docs/pii.md` 定義整個系統的 PII 政策（認證、加密、同意、資料保留）。
本文件是 ASR 子系統的**特化補充**：

- ASR 特有的「不可記錄」清單比通用 PII 政策更嚴格（例如逐字稿不進任何日誌）
- 音訊生命週期規則是 ASR 獨有的（通用 PII 政策不涉及即時音訊處理）
- 遙測 allowlist 是 ASR 模組自身的安全邊界，通用政策不規定具體欄位

兩份文件互為補充，不重複：ASR 開發者兩者都需要閱讀。
