# SageMaker Inference Container Contract

本文件定義 ASR SageMaker real-time endpoint 的 Lambda-facing 契約。
未來 container 實作者必須依此文件建立 Lambda 相容的 endpoint。

相關文件：
- 架構入口：[`docs/asr/framework.md`](./framework.md)
- 設定規格：[`docs/asr/config-schema.md`](./config-schema.md)
- Lambda 端實作：`backend/src/shared/asr/remote_endpoints.py`
- Terraform 端點定義：`terraform/asr_models.tf`

---

## 1. Endpoint 類型

SageMaker **real-time inference endpoint**（非 async、非 batch transform）。

兩個端點共用相同契約：

| Endpoint | Metadata key |
|---|---|
| `ai-elder-care-asr-ce` | `taiwan_tongues_ce` |
| `ai-elder-care-asr-formo` | `formospeech_whisper_v3` |

模型 ID、支援語言、授權與核准狀態見
[`docs/asr/model-catalog.md`](./model-catalog.md)。

---

## 2. Health Check

Container 必須在 `/ping` 回應 HTTP 200，body 為空或任意內容。
SageMaker 以此判斷 container 是否就緒。

---

## 3. Invocation Entrypoint

`POST /invocations`

### 3.1 請求

| 欄位 | 值 | 說明 |
|---|---|---|
| `Content-Type` | `application/octet-stream` | 固定值 |
| `Accept` | `application/json` | 固定值 |
| `X-Amzn-SageMaker-Custom-Attributes` | 見下方 | 分號分隔的 key=value |
| Body | raw PCM bytes | 見 §4 |

### 3.2 CustomAttributes 格式

```
language={language_code};sample_rate_hz=16000;channels=1
```

| Key | 值域 | 說明 |
|---|---|---|
| `language` | `zh-TW` \| `hak` | 辨識語言 |
| `sample_rate_hz` | `16000` | 固定 16 kHz |
| `channels` | `1` | 固定單聲道 |

Container 只能收到這三個欄位。Lambda **不**傳送：
- prompt ID
- correlation ID
- elder ID
- HF token
- endpoint name
- 任何其他 metadata

---

## 4. 輸入音訊格式

| 屬性 | 值 |
|---|---|
| 編碼 | PCM signed 16-bit little-endian (S16LE) |
| 取樣率 | 16,000 Hz |
| 聲道數 | 1（mono） |
| 最大長度 | 60 秒（960,000 samples = 1,920,000 bytes） |

Body 是 **raw PCM bytes**，不是 WAV/M4A 容器格式、不是 base64。

---

## 5. 成功回應

HTTP 200，Content-Type `application/json`：

```json
{
  "text": "辨識結果文字"
}
```

規則：
- `text` 必須是非空白 Unicode 字串（Unicode trim 後非空）。
- 不得包含原始 audio bytes 或 PCM samples。
- 允許附帶額外欄位（如 `confidence`），Lambda 只取 `text`，忽略其餘。
- 空字串、null、非字串的 `text` 視為無效回應（Lambda 映射為 `provider_invalid_response`）。

---

## 6. 錯誤回應

Container 應以 HTTP 狀態碼表達錯誤：

| HTTP Status | 情境 | Lambda 映射 |
|---|---|---|
| 5xx | 模型載入失敗、推論例外、記憶體不足 | `provider_failure` |
| 503 | 模型未就緒（warming up） | `provider_unavailable`（透過 `ModelNotReadyException`） |
| 429 | 推論佇列已滿 | `provider_unavailable`（透過 `ThrottlingException`） |

錯誤 body 格式不拘（Lambda 不解析錯誤 body）。

SageMaker SDK 會將 container 回傳的非 2xx 轉換為 `ClientError`，Lambda 端靠 error code 分類。

---

## 7. 逾時

| 設定 | 預設值 | 說明 |
|---|---|---|
| Lambda read timeout | 30 秒 | `RemoteEndpointSpec.read_timeout_seconds` |
| Lambda connect timeout | 5 秒 | `RemoteEndpointSpec.connect_timeout_seconds` |
| SageMaker model timeout | 60 秒 | SageMaker endpoint 設定（`ModelDataDownloadTimeoutInSeconds`） |

Container 的推論必須在 Lambda read timeout 內完成。超時由 botocore 拋出 `ReadTimeoutError`，Lambda 映射為 `deadline_exceeded`。

---

## 8. Formo 方言 Prompt 邊界

**Lambda 不傳送 prompt ID。**

Formo 的六個方言 prompt（`htia_sixian`、`htia_hailu` 等）各自固定在：
- Container 的環境變數，或
- SageMaker endpoint 的 deployment 設定

Lambda 依 elder profile 腔調選擇對應的固定 endpoint；prompt 仍不是 per-request 參數，
推論 payload 與 CustomAttributes 都不得出現 prompt ID。

`asr_enable_endpoints = false` 時，六個 Formo endpoint 必須全部不存在。

---

## 9. PII 與日誌限制

Container **不得**將以下內容寫入日誌（CloudWatch Logs 或其他）：
- 音訊 bytes 或 PCM samples
- 逐字稿（辨識結果文字）
- HF token
- 長者個資（elder_id、姓名、通訊資訊）
- Lambda 傳入的 CustomAttributes 原始值
- provider 原始回應的完整 JSON

允許記錄的診斷資訊：
- 請求的 audio duration（秒數或 byte count）
- 推論延遲（毫秒）
- 成功/失敗狀態
- 模型版本識別
- 錯誤分類（不含原始例外訊息）

---

## 10. Contract Test Fixture

以下 Python fixture 可用於驗證 container 的 contract 相容性：

```python
"""最小 contract smoke test — 不需要真實模型。"""

import json
import requests

ENDPOINT_URL = "http://localhost:8080"  # 本地測試用

def test_ping():
    """Health check 必須回 200。"""
    r = requests.get(f"{ENDPOINT_URL}/ping")
    assert r.status_code == 200

def test_invocation_returns_json_with_text():
    """送出最小合法 PCM，回應必須含 text 欄位。"""
    # 0.1 秒靜音 = 1600 samples * 2 bytes = 3200 bytes
    silence_pcm = b"\x00\x00" * 1600

    r = requests.post(
        f"{ENDPOINT_URL}/invocations",
        data=silence_pcm,
        headers={
            "Content-Type": "application/octet-stream",
            "Accept": "application/json",
            "X-Amzn-SageMaker-Custom-Attributes": (
                "language=zh-TW;sample_rate_hz=16000;channels=1"
            ),
        },
    )
    assert r.status_code == 200
    payload = r.json()
    assert "text" in payload
    assert isinstance(payload["text"], str)

def test_invalid_audio_returns_error():
    """空 body 應回 4xx 或 5xx，不是 200。"""
    r = requests.post(
        f"{ENDPOINT_URL}/invocations",
        data=b"",
        headers={
            "Content-Type": "application/octet-stream",
            "Accept": "application/json",
            "X-Amzn-SageMaker-Custom-Attributes": (
                "language=zh-TW;sample_rate_hz=16000;channels=1"
            ),
        },
    )
    assert r.status_code >= 400
```

---

## 11. 模型差異的邊界

CE 與 Formo 的模型框架、語言、授權、存取方式與核准狀態以
[`docs/asr/model-catalog.md`](./model-catalog.md) 為準。這些差異只存在於
endpoint container 與部署流程；兩者面對 Lambda 的 I/O 契約完全相同。
