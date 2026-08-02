# BreezyVoice 推論容器（台灣華語）

`MediaTek-Research/BreezyVoice` 的 SageMaker real-time endpoint 容器。用途是把 `zh-TW`
路由從 Polly `Zhiyu`（`cmn-CN`，非台灣口音）換成台灣華語。

- 契約：[`docs/tts/sagemaker-inference-contract.md`](../../docs/features/tts/sagemaker-inference-contract.md)
- 授權與核准狀態：[`docs/tts/model-catalog.md`](../../docs/features/tts/model-catalog.md)
- 安全與 PII：[`docs/tts/security-and-pii.md`](../../docs/features/tts/security-and-pii.md)

## 先讀這段：聲音從哪裡來

BreezyVoice 是 zero-shot 模型，**沒有內建音色**。它的聲音完全複製自一段參考音檔
（speaker prompt）與該音檔的逐字稿。因此：

- App 的聲音等於你在打包階段放進 artifact 的那段錄音，換聲音要重新打包並重建 endpoint。
- 參考音檔在建置期固定，執行期不可更換；App 不能指定 speaker，符合契約中
  「container 啟動時固定 default speaker」的要求。
- **不得使用長者本人的聲音**。專案不做 voice cloning、不保存長者聲紋，參考音檔必須是
  已取得授權可商用／可展示的錄音，並保留來源與同意證明。

參考音檔建議 5–15 秒、單一講者、無背景音、取樣率不限（打包時會轉成 16 kHz 單聲道）。

### 先用上游附的範例音檔

不必為了跑通流程先去錄音：上游 repo 附了一段可直接使用的女聲
（`data/example.wav`，16 kHz 單聲道、約 17 秒），逐字稿在同目錄的 `batch_files.csv`。
先用它把整條 pipeline 走通、確認音質，再決定要不要換成自己的錄音。

兩點要注意：

- **逐字稿要用 `batch_files.csv` 那版（完整兩句），不是 README 範例指令裡的第一句。**
  上游範例指令只截了前半句，與 17 秒的音檔對不上；逐字稿與音檔不一致會讓合成品質下降。
- 這是真人錄音。模型本身是 Apache-2.0，但上游沒有單獨聲明這段錄音的權利範圍，
  正式對外前必須釐清，屬於 `license_cleared` 的一部分。

## 檔案

| 檔案 | 職責 |
|------|------|
| `Dockerfile` | CUDA 11.8 + Python 3.10 基底，clone 上游 repo 並裝好推論依賴 |
| `serve` | SageMaker 進入點（`docker run <image> serve`），固定 8080 埠 |
| `app/config.py` | 讀 terraform 注入的環境變數，缺漏即啟動失敗 |
| `app/contract.py` | request 欄位驗證；不依賴 fastapi，便於單獨測試 |
| `app/synthesizer.py` | 包裝上游 `CustomCosyVoice`，載入模型與固定聲紋 |
| `app/audio.py` | 22050 Hz 波形經 ffmpeg 轉 MP3 |
| `app/main.py` | `/ping` 與 `/invocations` 的 HTTP 與錯誤映射 |
| `scripts/build_and_push.sh` | 建映像並推 ECR（順便建 repository） |
| `scripts/package_model.sh` | 下載權重＋封入聲紋 → `model.tar.gz` → S3 |

## model artifact 結構

`model_data_url` 指向的 tar.gz 會被 SageMaker 解壓到 `/opt/ml/model`：

```text
/opt/ml/model/
├── breezyvoice/          # Hugging Face 權重快照
└── speakers/
    └── default/
        ├── prompt.wav    # 16 kHz 單聲道參考音檔
        └── prompt.txt    # 該音檔的逐字稿
```

## 部署步驟

前置：Docker（含 buildx）、AWS CLI 已登入、`pip install huggingface_hub`、ffmpeg。

```bash
# 1. 建映像並推上 ECR（在 Apple Silicon 上會自動跨平台建 linux/amd64）
#    --ref 建議釘 commit SHA，才能重現同一份推論行為
./scripts/build_and_push.sh --region us-west-2 --ref <commit-sha>

# 2. 打包 model artifact（bucket 需先存在）
#    這裡用上游附的範例女聲；換自己的錄音就改 --prompt-audio 與 --prompt-text，
#    逐字稿必須與音檔內容完全一致。
curl -sLO https://raw.githubusercontent.com/mtkresearch/BreezyVoice/main/data/example.wav

./scripts/package_model.sh \
  --prompt-audio ./example.wav \
  --prompt-text "在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。" \
  --bucket e-hakka-care-tts-artifacts

# 3. 把兩個腳本印出的 URI 填進 terraform/terraform.tfvars（範例見 terraform.tfvars.example），
#    先只開 endpoint、不開 approval gate：
#      tts_enable_breezyvoice_endpoint = true
#      tts_breezyvoice_approved        = false
cd ../../terraform && terraform apply
```

此時 endpoint 已存在但 Lambda 仍走 Polly——[`router.py`](../../backend/src/shared/tts/router.py)
的 `_is_eligible` 會因 gate 未核准而略過它。這是刻意的：先讓你在不影響線上語音的情況下驗證。

## 驗收後才開 gate

直接打 endpoint 驗證（不經過 Lambda）：

```bash
aws sagemaker-runtime invoke-endpoint \
  --endpoint-name e-hakka-care-tts-breezyvoice \
  --content-type application/json \
  --accept audio/mpeg \
  --body '{"text":"阿嬤早安，今天要記得吃藥喔。","language":"zh-TW","format":"mp3"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/out.mp3 && afplay /tmp/out.mp3
```

[`docs/tts/model-catalog.md`](../../docs/features/tts/model-catalog.md) 要求下列證據齊全才能把
`tts_breezyvoice_approved` 設為 true：

- [ ] 繁體中文輸入不出現錯字、破音字或簡體發音
- [ ] 聲線與長照情境相符，長輩可辨識
- [ ] 參考音檔的授權與同意證明已存檔
- [ ] 連續請求下容量穩定，單台 `ml.g4dn.4xlarge` 不排隊
- [ ] P95 不超過 8 秒（`/chat` 同步上限 28 秒，TTS 只佔其中一段）

全部通過後把 `tts_breezyvoice_approved = true` 再 apply 一次，`zh-TW` 才會真的改走
BreezyVoice，Polly Zhiyu 退為 fallback。

## 成本與收掉

常駐一台 `ml.g4dn.4xlarge`、無 autoscaling，約 US$1.5/hr（約 US$1,100/月）。競賽帳號
這個機型配額只有 1 台，見 `terraform/tts_models.tf` 的 `tts_endpoint_instance_quotas`。

不用時把 `tts_enable_breezyvoice_endpoint` 改回 `false` 並 apply，endpoint 才會真的收掉；
`zh-TW` 會自動退回 Polly，不需要改程式。

## 測試

契約與音訊轉換的單元測試不需要 GPU 或模型權重：

```bash
../../backend/.venv/bin/python -m pytest tests/ -q
```

模型載入與實際推論沒有單元測試覆蓋——那部分只能在建好映像後於 staging endpoint 驗證。
