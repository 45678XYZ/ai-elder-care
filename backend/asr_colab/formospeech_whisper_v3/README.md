# FormoSpeech Whisper-v3 — Colab 手動驗證套件

## 用途

本套件**僅供**人工在 Google Colab（免費 GPU runtime）上手動驗證 `formospeech/whisper-large-v3-taiwanese-hakka` 模型對合成/合法公開授權音檔的客語語音辨識能力。

**本套件不執行 Production Invocation、不呼叫 AWS adapter、不連接任何實際 AWS 服務/SDK/network。**

## 模型中繼資料

| 欄位 | 值 |
|------|-----|
| Model ID | `formospeech/whisper-large-v3-taiwanese-hakka` |
| 授權 | CC BY-NC 4.0 |
| 存取方式 | Gated Model（需申請 HuggingFace 存取權限） |
| 用途限制 | `colab_validation_only` |
| 支援語言 | 臺灣客語（hak） |
| 參數規模 | 約 20 億（2B parameters） |
| 基礎模型 | openai/whisper-large-v3 |

## 支援的 Formo Prompt ID

推論時必須指定客語腔調 Prompt ID，僅接受以下六個精確值：

- `htia_sixian` — 四縣腔
- `htia_hailu` — 海陸腔
- `htia_dapu` — 大埔腔
- `htia_raoping` — 饒平腔
- `htia_zhaoan` — 詔安腔
- `htia_nansixian` — 南四縣腔

不接受空白、大小寫變形、前後空白或 Unicode lookalike。

## 前置條件

1. Google Colab 帳號並選擇**免費 GPU runtime**
2. HuggingFace 帳號並已申請且取得 `formospeech/whisper-large-v3-taiwanese-hakka` 的 gated-model 存取權限
3. 將 HF Token 設定為 Colab Secret（名稱 `HF_TOKEN`）

## HF Token 安全規則

- Token **僅**從 Colab Secret 或短生命週期 runtime environment 讀取
- Token **不得**出現在：notebook source、cell output、requirements.lock、evidence 或 ADR

## 執行步驟

1. 在 Colab 中開啟 `validation.ipynb`
2. 選擇免費 GPU runtime（Runtime → Change runtime type → GPU）
3. 確認已在 Colab Secrets 中設定 `HF_TOKEN`
4. 依序執行所有 cell

## 輸入音檔

僅接受合成測試音檔或具合法公開使用授權的音檔，並以 `fixture_provenance.json` 記錄來源。支援格式：

- WAV（主要輸入）
- M4A（需解碼為 WAV）

## 輸出

每次驗證產生符合 `evidence.schema.json` 的結構化 JSON evidence record：

- 不含完整逐字稿（`transcript` 為禁止欄位）
- 不含 HF Token
- 不含音訊資料
- 不含 Formo Prompt ID

## 失敗處理

每項前置條件失敗均輸出：

```json
{
  "failure_prerequisite": "...",
  "failure_category": "...",
  "retry_step": "..."
}
```

涵蓋：GPU 不可用、依賴安裝失敗、模型下載失敗、gated-model 存取未核准、HF Token 缺失/無效、M4A 解碼失敗、Formo Prompt ID 無效。

## 檔案清單

| 檔案 | 用途 |
|------|------|
| `validation.ipynb` | Colab 驗證 notebook |
| `requirements.lock` | 精確釘選依賴版本 |
| `fixture_provenance.json` | 匿名 fixture 來源宣告 |
| `evidence.schema.json` | 模型專用 evidence schema |
| `README.md` | 本文件 |

## 禁止事項

- 不得執行 Production Invocation
- 不得呼叫 AWS adapter 或任何 AWS 服務/SDK/network
- 不得將 HF Token 寫入任何持久化位置
- 不得保存完整逐字稿至 evidence/ADR
- 不得使用真實長者資料
