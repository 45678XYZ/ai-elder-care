# FormoSpeech Whisper-v3 簡介

`formospeech/whisper-large-v3-taiwanese-hakka` 是由 **FormoSpeech** 團隊開發的**臺灣客語語音識別（ASR）微調模型**。

這套模型的亮點與規格簡述如下：

## 1. 模型核心與設計目的

- **基礎模型**：基於 OpenAI 的 `openai/whisper-large-v3` 進行微調。
- **主要目的**：驗證在多腔調微調時，加入各腔調專屬的 Prompt ID 是否能提升客語語音辨識的準確率與效果。

## 2. 支援的臺灣客語腔調

模型針對臺灣主要的 6 種客語腔調進行訓練，推論時可輸入對應的 Prompt ID 來優化辨識結果：

- **四縣腔** (`htia_sixian`)
- **海陸腔** (`htia_hailu`)
- **大埔腔** (`htia_dapu`)
- **饒平腔** (`htia_raoping`)
- **詔安腔** (`htia_zhaoan`)
- **南四縣腔** (`htia_nansixian`)

## 3. 訓練與開源授權規格

- **訓練參數**：Batch size 32、3 個 Epochs、Learning rate 7e-5、總訓練步數 42,549 steps。
- **模型規模**：約 20 億參數（2B parameters）。
- **開源授權**：**CC BY-NC 4.0**（僅供非商業用途使用，且需註明出處）。
- **使用限制**：屬於受控模式（Gated Model），需登入 Hugging Face 帳號並同意條款申請存取權限後，使用 Token 才可進行推論。(目前權限還在申請中)

## 4. 連結

[Hugging Face](https://huggingface.co/formospeech/whisper-large-v3-taiwanese-hakka)