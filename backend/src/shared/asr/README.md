# ASR 領域套件

本套件把 WAV／M4A 正規化為 mono 16 kHz PCM，再依注入的 ASR 設定呼叫受控的
Amazon Transcribe Streaming 或已核准 SageMaker endpoint。它不處理 HTTP、認證、session、
資料庫或聊天流程。

## 執行路徑

```text
AsrFacade → canonical_audio → AsrRouter → provider fallback → Transcript／TypedAsrError
                                               └────────────→ SafeTelemetryRecord
```

| 檔案 | 職責 |
|---|---|
| `types.py` | 音訊、語言、deadline、取消、逐字稿與 typed error |
| `canonical_audio.py` | WAV／M4A 驗證、解碼、downmix、resample 與 60 秒限制 |
| `config.py` | route、provider、model gate 與 ASR 設定 parser |
| `providers.py` | provider protocol、本機測試 mock、Transcribe Streaming 與 SageMaker adapters |
| `router.py`、`facade.py` | 依序 fallback 的 fail-closed 路由與單一入口 |
| `telemetry.py` | 每次請求一筆 allowlist 終態遙測 |
| `composition.py` | 環境設定、受控 provider registry 與 warm-start facade |

## 修改導覽

- 路由與執行邊界：[`docs/asr/framework.md`](../../../../docs/features/asr/framework.md)
- 設定：[`docs/asr/config-schema.md`](../../../../docs/features/asr/config-schema.md)
- 遠端 provider I/O：[`docs/asr/sagemaker-inference-contract.md`](../../../../docs/features/asr/sagemaker-inference-contract.md)
- PII／遙測：[`docs/asr/security-and-pii.md`](../../../../docs/features/asr/security-and-pii.md)
- 模型與核准：[`docs/asr/model-catalog.md`](../../../../docs/features/asr/model-catalog.md)

只讀與本次修改相關的文件。公開 API 契約仍以 `docs/api.md` 為準。

## 固定邊界

- Lambda remote-only；不得加入或載入模型推論框架。Amazon Transcribe 屬允許的受控遠端
  provider，不違反此邊界。
- `amazon_transcribe_zh_tw` 內部固定 Streaming、`zh-TW`、16 kHz、PCM；不使用 batch/S3。
- Lambda 不做程序內 admission／併發排隊；容量由 AWS service／SageMaker endpoint 管理。
- 注入的 ASR 設定是唯一來源（Region 除外），由 `ASR_CONFIG_JSON` 或
  `ASR_CONFIG_SSM_PARAMETER` 提供；解析或取得失敗都不退回預設值。
- 中文固定 Transcribe → CE；客語六腔固定 Formo → CE。只有三種 provider error 可 fallback。
- 未核准 route/provider 不外呼；Formo prompt 與 `Chinese` generation language 固定在每腔
  endpoint 部署設定，Lambda request 都不傳。
- 不記錄音訊、逐字稿、個資、token、endpoint、原始回應或原始例外。
- ASR 模型核准只採指定 SageMaker instance 的 staging/runtime evidence。

## 驗證

```powershell
cd backend
python -m pytest tests/asr -q
python -m pytest tests/asr/test_chat_asr_bridge.py -q
```
