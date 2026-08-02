# FormoSpeech Whisper-v3 推論容器（客語六腔）

`formospeech/whisper-large-v3-taiwanese-hakka` 的 SageMaker real-time endpoint 容器，
是六個客語腔調 route 的主力。依模型選型結論（見 [`docs/features/model_selection_asr_tts.md`](../../docs/features/model_selection_asr_tts.md)），
客語 ASR 只有這個模型支援六腔（MOS 3.21/5、平均 0.65s/句）。

- 契約：[`docs/asr/sagemaker-inference-contract.md`](../../docs/features/asr/sagemaker-inference-contract.md)
- 授權與核准狀態：[`docs/asr/model-catalog.md`](../../docs/features/asr/model-catalog.md)
- 安全與 PII：[`docs/asr/security-and-pii.md`](../../docs/features/asr/security-and-pii.md)

## 一個映像、六個 endpoint

腔調不進映像：`FORMO_PROMPT_ID` 由 terraform 逐 endpoint 注入，容器啟動時讀一次並固定。
所以六腔共用同一份映像與同一份 model artifact，**build 與 package 各跑一次就好**，
換腔調不必重建。

`FORMO_GENERATION_LANGUAGE=Chinese` 是 Whisper 的客語漢字解碼設定，不代表這個 endpoint
支援 `zh-TW`；容器會擋掉 `language=zh-TW` 的 request。

## 檔案

| 檔案 | 職責 |
|------|------|
| `Dockerfile` | CUDA 12.1 + Python 3.10 基底，裝 transformers 推論依賴 |
| `serve` | SageMaker 進入點（`docker run <image> serve`），固定 8080 埠 |
| `app/config.py` | 讀 terraform 注入的環境變數，缺漏或 prompt 不合法即啟動失敗 |
| `app/contract.py` | 驗證 PCM body 與 CustomAttributes；不依賴 fastapi/torch，便於單獨測試 |
| `app/transcriber.py` | 包裝 transformers ASR pipeline，啟動時算好 `prompt_ids` |
| `app/main.py` | `/ping` 與 `/invocations` 的 HTTP 與錯誤映射 |
| `scripts/build_and_push.sh` | 建映像並推 ECR |
| `scripts/package_model.sh` | 下載 gated 權重 → `model.tar.gz` → S3 |

## model artifact 結構

`model_data_url` 指向的 tar.gz 會被 SageMaker 解壓到 `/opt/ml/model`：

```text
/opt/ml/model/
└── formospeech/          # Hugging Face 權重快照（safetensors）
```

## 部署步驟

前置：Docker（含 buildx）、AWS CLI 已登入、`pip install huggingface_hub`，以及**已取得
gated repository 存取權的 Hugging Face token**。

```bash
# 1. 建映像並推上 ECR
./scripts/build_and_push.sh --region us-west-2

# 2. 打包 model artifact（約 3 GB；HF_TOKEN 只在這一步使用）
HF_TOKEN=hf_xxx ./scripts/package_model.sh --bucket e-hakka-care-asr-artifacts-<account>

# 3. 把兩個 URI 填進 terraform/terraform.tfvars，先只開 endpoint、不開 approval gate
cd ../../terraform && terraform apply
```

HF token 依 [`docs/asr/model-catalog.md`](../../docs/features/asr/model-catalog.md) 的存取規則，
只可在本機／CI 的打包階段短暫注入，**不得進入映像、SageMaker environment 或 Lambda**。

## 驗收後才開 gate

直接打其中一個 endpoint（不經過 Lambda）：

```bash
# 準備 16 kHz 單聲道 PCM S16LE，最長 60 秒
ffmpeg -i sample.wav -ac 1 -ar 16000 -f s16le -acodec pcm_s16le /tmp/sample.pcm

aws sagemaker-runtime invoke-endpoint \
  --endpoint-name e-hakka-care-asr-formo-sixian \
  --content-type application/octet-stream \
  --accept application/json \
  --custom-attributes 'language=hak;sample_rate_hz=16000;channels=1' \
  --body fileb:///tmp/sample.pcm \
  /tmp/out.json && cat /tmp/out.json
```

六腔各驗一次，確認辨識品質與 P95 後，才依
[`docs/adr/asr-formo-production-approval.md`](../../docs/features/adr/asr-formo-production-approval.md)
更新 production gate。建立 endpoint 不等於核准。

## 成本

六個 endpoint 各固定一台、無 autoscaling，機型分配見
[`docs/asr/model-catalog.md`](../../docs/features/asr/model-catalog.md)：

| 機型 | 台數 | $/hr |
|---|---|---|
| `ml.g5.2xlarge`（四縣、海陸） | 2 | 1.515 |
| `ml.g5.xlarge`（大埔、饒平） | 2 | 1.408 |
| `ml.g4dn.2xlarge`（詔安、南四縣） | 2 | 0.940 |

合計約 **US$7.73/hr（約 US$5,640/月）**。不用時把 `asr_enable_endpoints` 改回 `false`
並 apply，endpoint 才會真的收掉。

## 測試

契約與 PCM 轉換的單元測試不需要 GPU 或模型權重：

```bash
../../backend/.venv/bin/python -m pytest tests/ -q
```

模型載入與實際辨識沒有單元測試覆蓋——那部分只能在建好映像後於 staging endpoint 驗證。
