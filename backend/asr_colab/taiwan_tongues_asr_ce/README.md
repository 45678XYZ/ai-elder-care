# Taiwan-Tongues-ASR-CE 手動 Colab 驗證套件

## 目的

本套件**僅供人工 Colab 手動驗證** Taiwan-Tongues-ASR-CE 語音辨識模型，確認模型可正常載入、推論並產出符合 evidence schema 的去識別化結構化證據。

**本套件不做、也不宣稱 CE 的 production invocation。**

模型固定規格與核准狀態見
[`docs/asr/model-catalog.md`](../../../docs/asr/model-catalog.md)。

---

## 套件結構

```
taiwan_tongues_asr_ce/
├── README.md                  ← 本文件
├── validation.ipynb           ← Colab 驗證 notebook（JSON 格式）
├── requirements.lock          ← 精確釘選依賴
├── fixture_provenance.json    ← 匿名 fixture 來源宣告
└── evidence.schema.json       ← 本套件 evidence schema（引用父層）
```

---

## 執行先決條件

1. **免費 GPU runtime** — 於 Google Colab 選擇 Runtime → Change runtime type → T4 GPU。
2. **依賴安裝** — 使用 `requirements.lock` 中精確版本安裝所有依賴。
3. **模型下載** — 由 Hugging Face Hub 下載 `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0`（無需 token）。
4. **音訊 fixture** — 僅使用 synthetic 或具合法公開授權的音檔，對應 `fixture_provenance.json` 宣告。

---

## 驗證流程

1. GPU preflight：偵測 GPU 可用性，失敗時輸出分類與 retry step。
2. 依賴安裝：以精確版本安裝 `requirements.lock`。
3. 模型下載：下載 CTranslate2 格式模型。
4. WAV 輸入處理：載入 WAV fixture，執行推論。
5. M4A 解碼處理：解碼 M4A 為 WAV 後推論；解碼失敗時輸出分類與 retry step。
6. Evidence 輸出：產出符合 schema 的去識別化 JSON evidence record。

---

## 前置條件失敗處理

每項前置條件失敗時，notebook 輸出結構化 JSON：

```json
{
  "failure_prerequisite": "（失敗的前置條件描述）",
  "failure_category": "（失敗分類）",
  "retry_step": "（可執行的重試步驟）"
}
```

---

## 安全禁止事項

- 不得執行 CE 的 production invocation
- 不得呼叫任何 AWS service、SDK 或 network
- 不得將完整 transcript、audio bytes 或 token 寫入 evidence
- Evidence 僅允許 schema 定義的去識別化欄位

---

## 適用需求

本套件涵蓋需求：3.5, 3.6, 6.1, 6.2, 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11。
