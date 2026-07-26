# Taiwan-Tongues-ASR-CE 簡介

## 1. 模型簡介新

- **基底模型**：基於 `openai/whisper-large-v2` 進行微調與優化。
- **部署格式**：已轉換為 **CTranslate2 / faster-whisper** 格式（提供 `model.bin` 等推論檔），專門用於高效能推論與批次語音轉錄。

## 2. 支援語言與代碼 (輸出文字不確定是哪種語言)

模型共支援 5 種語言：

- **華語（Mandarin Chinese）**：`zh`
- **台語/台灣閩南語（Taiwanese Hokkien）**：`nan`
- **客語（Hakka）**：`hak`
- **英語（English）**：`en`
- **印尼語（Indonesian）**：`id`

## 3. 使用注意事項

- **授權規範**：標示為 `other`，商業使用或二次散布前需留意發布規範與許可合約。

## 4. 快速使用範例 (Python)

安裝套件後即可直接透過 `faster-whisper` 載入模型：

Python

```
from faster_whisper import WhisperModel

# 載入推論模型
model = WhisperModel(
    "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
    device="cuda",
    compute_type="float16"
)

# 執行語音轉錄（以印尼語為例，可替換為 zh, nan, hak, en, id）
segments, info = model.transcribe(
    "audio.wav",
    language="id",
    task="transcribe"
)

for segment in segments:
    print(segment.text)
```

## 5. 模型連結

* [Hugging Face](https://huggingface.co/adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0)
* [GitHub](https://github.com/adi-gov-tw/Taiwan-Tongues-ASR-CE)
