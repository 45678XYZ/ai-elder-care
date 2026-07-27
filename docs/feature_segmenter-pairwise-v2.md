# 有監督對話分塊模型（`pairwise_v2`）工作流

`CHUNKER_TYPE=pairwise_v2` 用的模型不在這個 repo 訓練——訓練需要語料與 sklearn，屬離線工作；
本專案只放**執行期推論**與**artifact 契約**。這份文件是兩邊的接合說明與完整操作指示。

- 執行期推論：[`backend/src/extraction/segmenter.py`](../backend/src/extraction/segmenter.py)
- 離線工作流：`aws-hackathon/segmenter_v2/` 與 `aws-hackathon/scripts/segmenter_v2_*.py`
- 移植決策脈絡：[feature_events-extraction.md](feature_events-extraction.md) §6、§7

## 1. 為什麼不是直接沿用舊模型

舊的 `pairwise_segmenter_model.pkl` 有兩層問題，兩層都不是換個檔案就能解決的：

1. **TF-IDF 版對中文退化**。`TfidfVectorizer(max_features=500, token_pattern=r"(?u)\b\w+\b")`
   在無空白的中文上會把整句抓成一個 token，向量近全零、特徵退化成常數；配上
   `p >= 0.25 and curr_len >= 3` 的門檻，10 輪對話會機械切在 `[0, 3, 6]`。上游已加零向量偵測。
2. **多語言句向量版沒有 held-out，且綁死座標系**。只用一段對話、9 個 turn pair 訓練並在同一份
   資料上評估，772 維特徵配 9 筆樣本必然完全記憶；更關鍵的是特徵 `abs_diff`／`dot_prod` 綁死
   MiniLM-384 的座標系，換 embedding 模型就等於換座標系，訓練好的樹全部失效。

所以這一版重做三件事：**特徵與 embedding 維度無關**、**切分一律 by dialogue_id**、
**artifact 是純 Python 資產**（不帶 pickle、不帶 sklearn 執行期依賴）。

## 2. 契約：feature spec 與 artifact 格式

特徵定義只有一份實作，在 `backend/src/extraction/segmenter.py` 的 `FEATURE_SPEC` 與
`extract_features()`。離線工作流透過 `segmenter_v2/contract.py` **匯入**這份實作（把
`ai-elder-care/backend` 推到 `sys.path` 最前面），而不是複製一份——訓練與推論抽的特徵必須
逐位相同，否則模型在線上等於用錯座標系。這也是兩個 repo 的頂層套件都叫 `src`，卻把工作流
套件放在 `aws-hackathon/` 根目錄的原因：`src` 一律解析到本專案後端。

13 個特徵都是尺度不變的統計量，換 embedding 模型只要重抽特徵重訓，`FEATURE_SPEC` 不用動：

| 群組 | 特徵 |
|---|---|
| 相似度 | `adjacent_cosine`、`depth_score`、`window_cosine_k2`、`window_cosine_k3` |
| 分布位置 | `cosine_percentile`、`cosine_delta_prev`、`cosine_delta_next` |
| 結構 | `position_ratio`、`speaker_changed` |
| 長度（對話內 z-score） | `left_length_z`、`right_length_z`、`length_diff_z` |
| 全域 | `center_similarity_delta` |

長度類特徵一律做**對話內 z-score**：訓練文本是機翻中文，字數分布與真實 ASR 逐字稿不同，
正規化後這類特徵才跨語言可轉移。

artifact 是單一 JSON（`assets/segmenter/pairwise_v2.json`）：

```json
{
  "artifact_version": "pairwise-v2-1",
  "embedding_model_id": "amazon.titan-embed-text-v2:0",
  "embedding_dim": 1024,
  "feature_spec": ["adjacent_cosine", "..."],
  "threshold": 0.35,
  "init_score": -1.42,
  "learning_rate": 0.1,
  "trees": [{ "feature": 0, "threshold": 0.5, "left": {"value": 0.3}, "right": {"value": -0.2} }],
  "model_card": { "labels": "...", "text": "...", "split": "...", "dev_metrics": {} }
}
```

載入端的檢查（`load_segmenter`）：`feature_spec` 與程式不符 → 拒絕載入；`trees` 為空 → 拒絕；
推論時 embedder 維度與 `embedding_dim` 不符 → 拋錯（換模型必須重訓，不可硬跑）。
artifact 不存在時回 `None`，分塊器退回機械切分並告警，不會安靜地改用別的模式。

導出端會**自我驗證**：比對 sklearn 的 `decision_function` 與 artifact 算出的原始分數，
偏差超過 `1e-6` 就拒絕輸出。這道檢查擋掉「導出公式與 sklearn 內部實作不一致」這種
只有上線後才會發現的錯。

## 3. 資料政策

分界線是：**訓練資料可以機器產，評測資料不行。** 合成對話的問題不是品質差，是反過來——
邊界會被寫得過於乾淨、指標虛高，而且產生者與稽核者同源，偏誤同源等於沒審。

### 訓練與開發集（機器可產）

| 集合 | 來源 | 段數 | 標籤 | 文本 |
|---|---|---|---|---|
| 訓練 | `tiage_train` + `tiage_validation` + `dialseg711_test` | 1111 | 真人 | 英文原文 → 逐 turn 機翻 zh-TW |
| 開發 | `tiage_test` | 100 | 真人 | 同上 |

實測語料規模：訓練集 24877 turns / 23766 個縫隙，正例率 16.6%；開發集 1564 turns、正例率 21.5%。

逐 turn 翻譯之所以安全：切分標籤是**位置型**的（落在第 i 與 i+1 之間），不是 span 型。
文字換了、turn 數不變，標籤位置原封不動。翻譯後由程式重組，標籤完整性是 by construction
的，不需要指望模型不去動 `[BOUNDARY]` 標記。

刻意排除 `data/clean_pairwise_dataset.jsonl`：其 SeniorTalk 部分是 `(i + 1) % 4 == 0` 的機械
假標，拿來訓練等於教模型「每 4 輪切一次」。

`superseg`（6948 段）分布與其他兩者不同，預設不納入，需要時 `--include-superseg`。

### 測試集三層（必須人工標邊界）

| 層 | 段數 | 對話來源 | 用途 |
|---|---|---|---|
| Test-Real | 20 | `BAAI/SeniorTalk` **原始轉錄**（只抽樣、清洗、編號，不改字） | **gate 主判定** |
| Test-Localized | 10 | `seniortalk_tw_balanced_corpus.jsonl`（真實結構 + LLM 在地化用詞） | 輔助，指標分開報 |
| Test-Scenario | 10–15 | `balanced_corpus.json`（LLM 生成的長照場景） | 輔助，**不列入 gate** |

Test-Real 用詞是大陸普通話而非台灣用語。對「話題邊界偵測」可接受——判的是語意流轉，
不是詞彙在地性；用詞在地化對事件萃取的影響大得多。

**待確認**：`BAAI/SeniorTalk` 的授權是否允許本次用途（尤其 NC 條款）。抽樣前先確認。

標註者只有一人時算不出標註者間一致性，因此判準要寫死（產出的 guidelines 已包含），
並在標完後隔一段時間重標 5 段算 self-agreement，作為結論可信度的下限。

## 4. 上線 gate

`pairwise_v2` 必須在**人工標註的 Test-Real** 上同時勝過兩個基線，才可設為 `CHUNKER_TYPE` 預設：

| 基線 | 放它的理由 |
|---|---|
| `every_3_turns` | 機械切分。舊版看起來 90% 準確、實際只是每 3 輪切一次；這條基線讓退化一眼可見 |
| `embedding_depth` | 無監督路徑，不需要訓練就能上線。贏不過它就沒有理由承擔訓練與 artifact 維運成本 |

判定條件：micro F1 **更高** 且 Pk **更低**（兩個基線都要滿足）。未通過就維持
`embedding_depth`，artifact 不上線。判定由 `segmenter_v2_evaluate.py` 自動輸出，不靠人工判斷。

指標同時看 P/R/F1 與 Pk／WindowDiff：切分對「差一格」特別敏感，只看 F1 會把差一格罰成
一個假陽性加一個假陰性，過度懲罰。另外回報容忍差一格的 F1，用來區分模型是「抓錯位置」
還是「完全沒抓到」——兩者的改善方向不同。

## 5. 操作步驟

前置：`aws-hackathon` 的 venv 需有 `boto3`、`scikit-learn`、`numpy`；
`AI_ELDER_CARE_BACKEND` 未設時預設同層的 `ai-elder-care/backend`。

```bash
cd aws-hackathon

# 1. 語料正規化（英文真標 → 本工作流格式）
python scripts/segmenter_v2_prepare_corpora.py

# 2. 逐 turn 翻譯成繁中，標籤位置沿用（含機械稽核；建議先 --limit 20 抽查語感）
python scripts/segmenter_v2_translate.py \
    --input data/segmenter_v2/train_en.jsonl --output data/segmenter_v2/train_zh.jsonl
python scripts/segmenter_v2_translate.py \
    --input data/segmenter_v2/dev_en.jsonl --output data/segmenter_v2/dev_zh.jsonl

# 3. 抽 turn embedding 並落地快取（重訓不再付費；中斷可續跑）
python scripts/segmenter_v2_embed.py \
    data/segmenter_v2/train_zh.jsonl data/segmenter_v2/dev_zh.jsonl \
    --model amazon.titan-embed-text-v2:0 --dim 1024

# 4. 訓練 + 導出 artifact（GroupKFold by dialogue_id、門檻在開發集上選）
python scripts/segmenter_v2_train.py \
    --train data/segmenter_v2/train_zh.jsonl --dev data/segmenter_v2/dev_zh.jsonl \
    --model amazon.titan-embed-text-v2:0 --dim 1024 --out results/segmenter_v2

# 5. 產生人工標註檔（三層各跑一次），標完後 --finalize
python scripts/segmenter_v2_prepare_annotation.py --tier real --count 20
python scripts/segmenter_v2_prepare_annotation.py --tier real \
    --finalize data/segmenter_v2/annotation/real_to_annotate.jsonl

# 6. 評測與 gate 判定
python scripts/segmenter_v2_evaluate.py \
    --test data/segmenter_v2/test_real_zh.jsonl \
    --artifact results/segmenter_v2/pairwise_v2.json \
    --model amazon.titan-embed-text-v2:0 --dim 1024

# 7. gate 通過才上線
cp results/segmenter_v2/pairwise_v2.json \
   ../ai-elder-care/backend/src/extraction/assets/segmenter/pairwise_v2.json
# 並把 terraform 的 chunker_type 改為 pairwise_v2
```

沒有 AWS 憑證時可先驗證整條鏈接得起來：

```bash
python scripts/segmenter_v2_smoke.py
```

它用確定性的假 embedding 跑完訓練、導出、評測與 gate 判定。**分數沒有意義**，
驗的是程式與契約，不是品質。

## 6. 換 embedding 模型時

index 維度與 artifact 都綁定訓練時的座標系，所以換模型是一整套動作：

1. 用新模型重跑步驟 3、4（快取檔按模型分開，不會互相污染）。
2. 建新的 S3 Vectors 索引（名稱帶模型與維度），舊索引保留以便回退。
3. 重跑步驟 6 的 gate；沒過就別換預設。
4. 切換 `EMBEDDING_MODEL_ID`、`EMBEDDING_DIM`、`CONCEPT_VECTOR_INDEX` 三個環境變數。

執行期會擋住不一致：artifact 的 `embedding_dim` 與 embedder 不符時直接拋錯，
不會拿舊座標系的模型硬算。

## 7. 已知限制

- 翻譯解決語言差，不解決領域差。訓練語料是英文任務型對話，不是台灣長者閒聊，也不是 ASR
  逐字稿（無標點、語助詞、重複、口誤）。這段落差只能靠 Test-Real 確認。
- 機翻文本有 translationese，詞彙與句法分布偏離自然口語；長度類特徵已做對話內正規化以降低影響。
- 跨語言零樣本遷移有前例（CobSeg 只用英文訓練、繁中測到 F1 72.7%），但那不保證本任務也成立，
  所以才有 gate。
- `min_turns` 保底規則預設關閉（0）。評測時要能關掉，否則分不清是模型好還是保底規則剛好對。
