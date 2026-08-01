# OmniVoice 推論容器（客語六腔）

`formospeech/omnivoice-hakka-community-1` 的 SageMaker real-time endpoint 容器，是六個
客語 TTS route 的主力。依 [`eval/MODEL_SELECTION.md`](../../eval/MODEL_SELECTION.md)，
OmniVoice 的 MOS 4.31/5 遠優於 VoxHakka 的 1.79/5，後者不納入部署。

- 契約：[`docs/tts/sagemaker-inference-contract.md`](../../docs/tts/sagemaker-inference-contract.md)
- 授權與核准狀態：[`docs/tts/model-catalog.md`](../../docs/tts/model-catalog.md)
- 安全與 PII：[`docs/tts/security-and-pii.md`](../../docs/tts/security-and-pii.md)

## 先讀這段：聲音從哪裡來

OmniVoice 跟 BreezyVoice 一樣是 zero-shot 模型，**沒有內建音色**。聲音完全複製自一段
參考音檔（`ref_audio`）與其逐字稿（`ref_text`）。因此：

- App 的客語聲音等於你在打包階段放進 artifact 的那段錄音，換聲音要重新打包並重建 endpoint。
- **不得使用長者本人的聲音**。專案不做 voice cloning、不保存長者聲紋，參考音檔必須是
  已取得授權的錄音，並保留來源與同意證明。
- 參考音檔建議 5–15 秒、單一講者、無背景音（打包時會轉成 16 kHz 單聲道）。

建議用**客語母語者**的錄音：參考音檔的口音會影響輸出，用華語錄音去合成客語效果會打折。

## 腔調怎麼來的

六腔透過上游的 `instruct` 參數指定，對照表固定在 `app/config.py`：

| wire value | instruct |
|---|---|
| `htia_sixian` | 客語四縣腔 |
| `htia_hailu` | 客語海陸腔 |
| `htia_dapu` | 客語大埔腔 |
| `htia_raoping` | 客語饒平腔 |
| `htia_zhaoan` | 客語詔安腔 |
| `htia_nansixian` | 客語南四縣腔 |

request 只能送 wire value，不能送任意 instruct 字串；沒帶 `dialect` 一律拒絕，不會
預設挑一腔。單一 endpoint 服務全部六腔，不像 ASR 要開六台。

## 檔案

| 檔案 | 職責 |
|------|------|
| `Dockerfile` | CUDA 12.1 + Python 3.10 基底，裝 FormoSpeech 的 OmniVoice-hakka fork |
| `serve` | SageMaker 進入點（`docker run <image> serve`），固定 8080 埠 |
| `app/config.py` | 讀 terraform 注入的環境變數與腔調對照表 |
| `app/contract.py` | request 欄位驗證；不依賴 fastapi，便於單獨測試 |
| `app/synthesizer.py` | 包裝 `OmniVoice.generate`，載入模型與固定聲紋 |
| `app/audio.py` | 24000 Hz 波形經 ffmpeg 轉 MP3 |
| `app/main.py` | `/ping` 與 `/invocations` 的 HTTP 與錯誤映射 |
| `scripts/build_and_push.sh` | 建映像並推 ECR |
| `scripts/package_model.sh` | 下載 gated 權重＋封入聲紋 → `model.tar.gz` → S3 |

## model artifact 結構

```text
/opt/ml/model/
├── omnivoice/            # Hugging Face 權重快照
└── speakers/
    └── default/
        ├── prompt.wav    # 16 kHz 單聲道客語參考音檔
        └── prompt.txt    # 該音檔的客語逐字稿
```

## 部署步驟

前置：Docker（含 buildx）、AWS CLI 已登入、`pip install huggingface_hub`、ffmpeg，
以及**已取得 gated repository 存取權的 Hugging Face token**。

```bash
# 1. 建映像並推上 ECR
./scripts/build_and_push.sh --region us-west-2

# 2. 打包 model artifact（HF_TOKEN 只在這一步使用，不進映像也不進 endpoint）
HF_TOKEN=hf_xxx ./scripts/package_model.sh \
  --prompt-audio /path/to/hakka_voice.wav \
  --prompt-text "參考音檔的客語逐字稿，需與音檔內容完全一致" \
  --bucket e-hakka-care-tts-artifacts-<account>

# 3. 填 terraform/terraform.tfvars，先只開 endpoint、不開 approval gate：
#      tts_enable_omnivoice_endpoint = true
#      tts_omnivoice_approved        = false
cd ../../terraform && terraform apply
```

此時 endpoint 已存在但客語 route 仍 fail closed——
[`router.py`](../../backend/src/shared/tts/router.py) 的 `_is_eligible` 會因 gate 未核准
而略過它。這是刻意的：先讓你在不影響線上語音的情況下驗證。

## 驗收後才開 gate

```bash
aws sagemaker-runtime invoke-endpoint \
  --endpoint-name e-hakka-care-tts-omnivoice \
  --content-type application/json \
  --accept audio/mpeg \
  --body '{"text":"食飽吂？今晡日愛記得食藥。","language":"hak","dialect":"htia_sixian","format":"mp3"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/out.mp3 && afplay /tmp/out.mp3
```

六腔各聽一次（換 `dialect`）。[`docs/tts/model-catalog.md`](../../docs/tts/model-catalog.md)
要求下列證據齊全才能把 `tts_omnivoice_approved` 設為 true：

- [ ] 六腔發音正確，母語者可辨識腔調差異
- [ ] 客語漢字輸入不出現錯讀或跳字
- [ ] 參考音檔的授權與同意證明已存檔
- [ ] 授權為 CC BY-NC 4.0——**專案轉商用前不得核准 `license_cleared`**
- [ ] P95 不超過 8 秒（eval 量到約 1.1s/句）

## 成本

一台 `ml.g4dn.xlarge`、無 autoscaling，約 **US$0.74/hr（約 US$540/月）**。
不用時把 `tts_enable_omnivoice_endpoint` 改回 `false` 並 apply。

## 測試

```bash
../../backend/.venv/bin/python -m pytest tests/ -q
```

模型載入與實際推論沒有單元測試覆蓋——那部分只能在建好映像後於 staging endpoint 驗證。

`app/audio.py` 與 BreezyVoice 容器的同名檔案內容相同：兩個映像各自獨立建置，共用檔案
會讓 build context 互相牽連，因此刻意不抽成共用模組。
