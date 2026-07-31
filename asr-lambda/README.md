# ASR 模型整合 — 容器開發與驗證環境

本目錄提供 ASR 模型的 conda 環境設定，定位為 **SageMaker inference container 開發與人工驗證**用途。

> **重要**：Lambda 不在 process 內執行模型推論（remote-only 架構）。
> 本環境用於建立與測試 inference container，不是 Lambda 執行環境。
> 架構說明見 [`docs/asr/framework.md`](../docs/asr/framework.md)。

## 涵蓋模型

模型 ID、語言、授權、存取方式與核准狀態統一以
[`docs/asr/model-catalog.md`](../docs/asr/model-catalog.md) 為準。
本目錄只記錄 container 開發方式：

- [`docs/Taiwan-Tongues-ASR-CE.md`](docs/Taiwan-Tongues-ASR-CE.md)
- [`docs/FormoSpeech Whisper-v3.md`](docs/FormoSpeech%20Whisper-v3.md)

## 前置需求

- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) 或 [Anaconda](https://www.anaconda.com/)
- NVIDIA GPU + 驅動（CUDA 12.4 相容）
- FormoSpeech 模型需事先申請 Hugging Face gated access 並取得 token

## 環境建立與啟用

```bash
# 建立環境（首次）
conda env create -f environment.yml

# 啟用
conda activate asr-model

# 更新（當 environment.yml 有變動時）
conda env update -f environment.yml --prune

# 移除環境
conda env remove -n asr-model
```

## 主要套件說明

| 類別 | 套件 | 用途 |
|------|------|------|
| GPU 推論 | `pytorch`, `torchaudio`, `cuda-toolkit` | CUDA GPU 加速推論 |
| ASR (CE) | `faster-whisper`, `ctranslate2` | Taiwan-Tongues CTranslate2 格式推論 |
| ASR (Formo) | `transformers`, `accelerate` | FormoSpeech Whisper-v3 推論 |
| 模型存取 | `huggingface_hub` | 模型下載、gated model token 驗證 |
| 音訊處理 | `ffmpeg`, `librosa`, `soundfile`, `pydub`, `av` | WAV/M4A 解碼、resampling、Canonical Audio 轉換 |
| 測試 | `pytest`, `hypothesis` | ASR-only 測試套件（與 `backend/` spec 一致） |

## 驗證環境是否正常

```bash
conda activate asr-model

# 確認 CUDA 可用
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

# 確認 faster-whisper 可 import
python -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"

# 確認 transformers 可 import
python -c "from transformers import WhisperForConditionalGeneration; print('transformers OK')"
```

## 與 backend/ 的關係

- `backend/src/shared/asr/` 是 Lambda remote-only 領域套件，不依賴本環境的模型推論套件。
- 本環境用來讓 inference container 接收 Lambda 產生的 mono／16 kHz／PCM S16LE。
- `backend/tests/asr/` 的測試使用 `pytest==8.3.5` 與 `hypothesis==6.122.3`，與本環境版本一致。
- Colab 驗證包（`backend/asr_colab/`）有各自獨立的 `requirements.lock`，本環境僅供本機開發使用。

## 注意事項

- 本環境需要 NVIDIA GPU；若無 GPU 僅能執行不涉及模型推論的單元測試。
- FormoSpeech 為 gated model，使用前需以 `huggingface-cli login` 登入已獲授權的帳號。
- 請勿將 HF token 寫入任何檔案或提交至版本控制。
