# ASR / TTS 模型評估（SageMaker）

## 目標

在 AWS SageMaker 上部署並評估專案使用的 ASR 與 TTS 開源模型，確認其在情境化對話腳本下的辨識與合成品質。

## 測試模型

| 類型 | Model | HuggingFace ID | 語言 | 推論框架 |
|---|---|---|---|---|
| ASR | Taiwan-Tongues-CE | `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0` | zh-TW, hak | faster-whisper (CTranslate2) |
| ASR | FormoSpeech Whisper-v3 | `formospeech/whisper-large-v3-taiwanese-hakka` | hak 六腔 | transformers |
| TTS | OmniVoice | `formospeech/omnivoice-hakka-community-1` | 客語六腔 | OmniVoice-hakka (voice cloning) |
| TTS | VoxHakka | `formospeech/yourtts-htia-240704` | 四縣/海陸/大埔/饒平/詔安 | YourTTS (Coqui TTS) |

## 目錄結構

```
eval/
├── README.md                 ← 本檔案
├── PROGRESS.md               ← 進度追蹤
├── MODEL_SELECTION.md        ← 模型選型結論報告
├── scripts/                  ← 情境化對話腳本（原始素材）
│   ├── demo-script.md
│   ├── elder_tts_asr_conversations.jsonl
│   └── scenario_script.md
├── corpus/                   ← 從腳本抽取的測試語料
│   ├── asr_test_utterances.jsonl    (25 筆 CE + 4 筆 Formo)
│   └── tts_test_utterances.jsonl    (5 筆 OmniVoice + 7 筆 VoxHakka)
├── notebooks/                ← SageMaker 評估 notebooks
│   ├── tts_eval_sagemaker.ipynb     (VoxHakka + OmniVoice 評估)
│   └── asr_eval_sagemaker.ipynb     (CE + Formo + Roundtrip 評估)
└── processing/               ← Processing Job 腳本（留作參考，quota=0 未使用）
    ├── tts_eval.py
    └── submit_tts_job.py
```

## 模型推論 API 摘要

### Taiwan-Tongues-CE (ASR)

```python
from faster_whisper import WhisperModel
model = WhisperModel("adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0", device="cuda", compute_type="float16")
segments, info = model.transcribe(audio_path, language="zh", beam_size=5, vad_filter=True)
```

### FormoSpeech Whisper-v3 (ASR)

```python
from transformers import WhisperProcessor, WhisperForConditionalGeneration
processor = WhisperProcessor.from_pretrained("formospeech/whisper-large-v3-taiwanese-hakka")
model = WhisperForConditionalGeneration.from_pretrained(...).to("cuda")
# forced_decoder_ids: language="chinese", task="transcribe"
```

### OmniVoice (TTS)

```python
from omnivoice import OmniVoice
model = OmniVoice.from_pretrained("formospeech/omnivoice-hakka-community-1", device_map="cuda:0", dtype=torch.float16)
audio = model.generate(text="...", ref_audio="ref.wav", ref_text="...", instruct="客語四縣腔")
# 輸出 24kHz，instruct 可選：客語四縣腔/海陸腔/大埔腔/饒平腔/詔安腔/南四縣腔
```

### VoxHakka (TTS)

```python
from formog2p.hakka import g2p
from TTS.utils.synthesizer import Synthesizer
# 1. G2P: g2p(text, "hak_sx", include_eng=True) → IPA
# 2. parse IPA → list
# 3. synth.tts(parsed_ipa, speaker_name="XF", language_name="sixian", split_sentences=False)
# 輸出 22050Hz，支援 sixian/hailu/dapu/raoping/zhaoan
```

## 評分維度

### ASR

| 維度 | 5 | 3 | 1 |
|---|---|---|---|
| 完整度 (Completeness) | 關鍵詞全部命中 | 漏 1-2 個 | 大部分漏掉 |
| 正確度 (Accuracy) | 語意完全正確 | 有錯但語意保留 | 語意錯誤 |
| 可用度 (Usability) | NLU 可直接用 | 需容錯處理 | 無法使用 |

### TTS

| 維度 | 5 | 3 | 1 |
|---|---|---|---|
| 清晰度 (Intelligibility) | 每個字都聽清楚 | 大致能懂 | 聽不清 |
| 自然度 (Naturalness) | 接近真人 | 有合成感但可接受 | 機械感強 |
| 聲調 (Tone accuracy) | 全部正確 | 偶有偏差 | 錯誤明顯 |
| 情感 (Appropriateness) | 符合情境 | 平淡但不突兀 | 不符合 |

## 注意事項

- **CC BY-NC 4.0**：OmniVoice 與 VoxHakka 皆為非商業授權，評估結果不構成 production 核准。
- **客語 G2P 詞庫有限**：VoxHakka 需要 G2P 轉換，未知詞會導致合成失敗。
- **OmniVoice 需要參考音檔**：voice cloning 架構，合成品質受 ref_audio 影響。
- **Roundtrip 測試**：TTS → ASR 的測試更貼近產品實際 pipeline。

## 評估結論

完整結論請見 [`MODEL_SELECTION.md`](MODEL_SELECTION.md)。摘要如下：

### 方案一：純中文 (zh-TW) — 推薦 MVP

| 環節 | 選用 | 備註 |
| --- | --- | --- |
| ASR | Amazon Transcribe Streaming `zh-TW` | AWS managed，低延遲 |
| TTS | Amazon Polly `zh-TW` Neural | AWS managed，自然度高 |

### 方案二：純客語 (hak) — 長期目標

| 環節 | 選用 | 備註 |
| --- | --- | --- |
| ASR | FormoSpeech Whisper-v3 | MOS 3.21/5，需 LLM 容錯 |
| TTS | OmniVoice | MOS 4.31/5，接近真人語感 |

### 關鍵數據

- **TTS**：OmniVoice MOS 4.31 >> VoxHakka MOS 1.79（VoxHakka 不適合面對長者）
- **ASR**：Taiwan-Tongues-CE MOS 4.67（中文優異）；FormoSpeech MOS 3.21（客語可用但有字級偏差）
- **Roundtrip**（TTS→ASR）：OmniVoice→CE MOS 3.39，OmniVoice→Formo MOS 3.21
- **授權**：OmniVoice / VoxHakka 皆 CC BY-NC 4.0，production 需另議授權
