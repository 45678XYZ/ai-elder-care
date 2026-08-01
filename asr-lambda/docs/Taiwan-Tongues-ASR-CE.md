# Taiwan-Tongues-ASR-CE Container 實作筆記

模型 ID、支援語言、授權、存取方式與核准狀態統一見
[`docs/asr/model-catalog.md`](../../docs/asr/model-catalog.md)。本文件只保留
SageMaker inference container 的實作注意事項。

CE 定位為中文與六腔客語的共同備援；未通過 staging/runtime、授權與容量 gate 前不得接受
production invocation。

## 推論格式

- 使用 CTranslate2／faster-whisper 載入模型 artifact。
- Container 對 Lambda 的介面必須遵守
  [`docs/asr/sagemaker-inference-contract.md`](../../docs/asr/sagemaker-inference-contract.md)。
- Container 收到 mono／16 kHz／PCM S16LE；不可把模型載入邏輯放進 Lambda。

## 輸出注意事項

- 回應固定為 `{ "text": "..." }`，文字必須非空白。
- 模型輸出文字使用的語言不保證；container 不得自行宣稱語言轉換成功。
- 不得記錄音訊、逐字稿或 provider 原始回應。

## 本機載入範例

```python
from faster_whisper import WhisperModel

model = WhisperModel(
    "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
    device="cuda",
    compute_type="float16",
)
```

這段只用於 container 開發與人工驗證，不可放入 Lambda 執行路徑。
