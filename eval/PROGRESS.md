# ASR / TTS 模型評估 — 進度與計畫

## 整體目標

在 AWS SageMaker Notebook Instance (ml.g5.xlarge, A10G 24GB GPU) 上評估客語 ASR 與 TTS 開源模型，確認其在情境化對話下的品質，作為 production 選型依據。

---

## AWS 資源現況

| 資源 | 名稱 / ARN | 狀態 |
|------|-----------|------|
| Notebook Instance | `tts-asr-eval` (ml.g5.xlarge) | **InService**（使用中計費） |
| S3 Bucket | `s3://e-hakka-care-eval-437814057855` | 已建立 |
| IAM Role | `arn:aws:iam::437814057855:role/SageMakerEvalRole` | 已建立 |
| Lifecycle Config | `tts-eval-setup`（安裝依賴） | 已附加到 instance |

**重要**：用完後必須停止 notebook instance 以停止計費：
```bash
aws sagemaker stop-notebook-instance --notebook-instance-name tts-asr-eval
```

---

## 已完成

- [x] 從舊分支 `eval/model-testing` 保留有用素材（情境腳本、語料、模型資訊）
- [x] 清除 AI tool configs（.codex, .kiro, AGENTS.md）從版控
- [x] 建立新分支 `eval/sagemaker-asr-tts`（從 main）
- [x] 整理測試語料 → `eval/corpus/`
  - `asr_test_utterances.jsonl`（29 筆：25 CE zh-TW + 4 Formo hak）
  - `tts_test_utterances.jsonl`（12 筆：5 OmniVoice + 7 VoxHakka）
- [x] 上傳語料到 S3 `input/` 前綴
- [x] 建立 SageMaker Notebook Instance + IAM Role + Lifecycle Config
- [x] 建立 TTS 評估 notebook → `eval/notebooks/tts_eval_sagemaker.ipynb`
- [x] 上傳 notebook 到 instance EBS（`/home/ec2-user/SageMaker/eval/tts_eval_sagemaker.ipynb`）
- [x] 上傳 notebook 到 S3 `notebooks/` 前綴（備份）
- [x] 建立 `eval/README.md`（模型 API 摘要、評分維度）
- [x] 建立 `skills/developing-ai-elder-care-speech/SKILL.md`
- [x] 執行 TTS 評估（VoxHakka 7/7 成功、OmniVoice 5/5 成功）
- [x] 修正 notebook 相容性問題（PyTorch 2.10 `total_memory`、`torchcodec`、`ffmpeg`）
- [x] 人工評分完成，CSV 已上傳 S3

---

## 未完成 / 待辦

### 1. 建立 ASR 評估 notebook（當前任務）

- [ ] `eval/notebooks/asr_eval_sagemaker.ipynb`
- 測試 Taiwan-Tongues-CE（faster-whisper）和 FormoSpeech Whisper-v3（transformers）
- 輸入：TTS 合成的音檔 + 預錄的測試音檔
- 評分：完整度 / 正確度 / 可用度
- Roundtrip 測試：TTS → ASR pipeline

### 2. Git 整理

- [ ] Commit 新檔案到 `eval/sagemaker-asr-tts` 分支：
  - `eval/notebooks/tts_eval_sagemaker.ipynb`
  - `eval/processing/tts_eval.py`（Processing Job 版，留作參考）
  - `eval/processing/submit_tts_job.py`（同上）
- [ ] 更新 `eval/README.md` 目錄結構（notebooks 已建立）
- [ ] 考慮刪除 `eval/processing/`（quota=0 無法使用）

### 3. 結果整理

- [ ] 從 S3 拉取評分 CSV
- [ ] 整理成結論報告（哪個模型適合 production pipeline）
- [ ] 更新 `eval/README.md` 加入結論

---

## 測試模型速查

| 類型 | Model | HuggingFace ID | 用途 |
|------|-------|----------------|------|
| TTS | VoxHakka | `formospeech/yourtts-htia-240704` | YourTTS + G2P，五腔客語 |
| TTS | OmniVoice | `formospeech/omnivoice-hakka-community-1` | Voice cloning，需 ref_audio |
| ASR | Taiwan-Tongues-CE | `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0` | faster-whisper，zh-TW |
| ASR | FormoSpeech Whisper-v3 | `formospeech/whisper-large-v3-taiwanese-hakka` | transformers，hak 六腔 |

**授權**：OmniVoice 與 VoxHakka 皆為 CC BY-NC 4.0（非商業）。

---

## 操作備忘

### 取得 Notebook URL（credentials 過期時需重新設定）

```bash
export AWS_DEFAULT_REGION=us-west-2
export AWS_ACCESS_KEY_ID=<your-key>
export AWS_SECRET_ACCESS_KEY=<your-secret>
export AWS_SESSION_TOKEN=<your-token>

# 啟動
aws sagemaker start-notebook-instance --notebook-instance-name tts-asr-eval

# 等待就緒
aws sagemaker wait notebook-instance-in-service --notebook-instance-name tts-asr-eval

# 取得 URL
aws sagemaker create-presigned-notebook-instance-url \
  --notebook-instance-name tts-asr-eval \
  --query "AuthorizedUrl" --output text

# 停止（省錢！）
aws sagemaker stop-notebook-instance --notebook-instance-name tts-asr-eval
```

### S3 結構

```
s3://e-hakka-care-eval-437814057855/
├── input/                    ← 測試語料 (jsonl)
├── code/                     ← Processing Job 腳本（未使用）
├── notebooks/                ← notebook 備份
└── output/                   ← 評估結果（TTS 音檔 + 評分 CSV）
```
