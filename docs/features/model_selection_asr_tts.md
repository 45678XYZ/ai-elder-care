# 語音模型選型結論

## Pipeline 結構

```
長者語音 → [ASR] → 文字 → [LLM: Claude Opus 4.6] → 回應文字 → [TTS] → 語音
```

---

## 方案一：純中文 (zh-TW)

| 環節 | 選用 | 備註 |
|------|------|------|
| ASR | Amazon Transcribe Streaming `zh-TW` | AWS managed |
| LLM | Claude Opus 4.6 (`us.anthropic.claude-opus-4-6-v1`) | — |
| TTS | Amazon Polly `zh-TW` Neural | AWS managed |

### 依據

**ASR — Amazon Transcribe 優於 Taiwan-Tongues-CE：**

- Transcribe 是 AWS managed 服務，低延遲 streaming、免維運
- Taiwan-Tongues-CE 在 eval 中 MOS 4.67/5、keyword hit rate 90.3%，表現優異但需自管 endpoint
- Transcribe 對台灣口語同樣有良好支援，且持續更新

**TTS — Amazon Polly 為最佳選擇：**

- 原生支援 `zh-TW` Neural voice，自然度高
- 本專案 TTS eval 未測試中文 TTS 模型（VoxHakka/OmniVoice 皆為客語專用）
- Polly 是 AWS managed，無需 GPU endpoint

---

## 方案二：純客語 (hak)

| 環節 | 選用 | 備註 |
|------|------|------|
| ASR | FormoSpeech Whisper-v3 (`formospeech/whisper-large-v3-taiwanese-hakka`) | SageMaker endpoint |
| LLM | Claude Opus 4.6 (`us.anthropic.claude-opus-4-6-v1`) | system prompt 需加入客語容錯指引 |
| TTS | OmniVoice (`formospeech/omnivoice-hakka-community-1`) | SageMaker endpoint |

### 依據

**ASR — FormoSpeech Whisper-v3：**

- 唯一支援客語六腔的 ASR 模型，eval MOS 3.21/5
- 辨識核心詞彙能力可接受（食飽、今晡日、毋使急、麼个）
- 字級偏差存在（咧→吔、秋妹嬸→就餓想、陳奶奶→塵泥泥），LLM 需容錯理解
- 平均延遲 0.65s/句，符合即時對話需求

**為何不用 Taiwan-Tongues-CE 做客語 roundtrip：**

- Roundtrip eval MOS 3.39/5，看似可用，但字面錯誤嚴重（嫦娥吃、乖乖、糟糕等噪音）
- 關鍵字保留約 60-70%，遇到華語中不存在的客語詞會完全丟失
- 非正式支援用法，品質不可預測，不適合作為 production baseline

**TTS — OmniVoice 優於 VoxHakka：**

| 指標 | OmniVoice | VoxHakka |
|------|-----------|----------|
| MOS | **4.31**/5 | 1.79/5 |
| 清晰度 | 4.75 | 3.00 |
| 自然度 | 4.50 | 1.57 |
| 聲調 | 4.25 | 1.14 |
| 情感 | 3.75 | 1.43 |
| 合成速度 | 1.1s/句 | 0.1s/句 |

- VoxHakka 速度極快但品質不可接受，機械感強烈，不適合面對長者
- OmniVoice 接近真人語感，聲調準確，適合關懷陪伴場景
- OmniVoice 支援 voice cloning（ref_audio），可客製長者熟悉的聲音風格

**LLM 容錯設計：**

- Claude Opus 4.6 能從上下文推斷客語 ASR 字級錯誤的原意
- System prompt 需明確指示：「ASR 輸入可能有客語同音字誤認，請依語境推斷原意」
- 不需額外開源 LLM（如 TAIDE）做 checker——增加延遲和故障點，且客語能力未驗證

---

## 評估數據來源

- 測試平台：AWS SageMaker ml.g5.xlarge (A10G 24GB)
- TTS eval：SageMaker notebook（VoxHakka 7/7、OmniVoice 5/5）
- ASR eval：SageMaker notebook（CE 25/25、Formo 8/8、Roundtrip 12/12）
- 評分方式：人工評分 1-5 量表，MOS = 各維度平均
- 測試語料：情境化長照對話（服藥提醒、健康回報、情緒支持等）