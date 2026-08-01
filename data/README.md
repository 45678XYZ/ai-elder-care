# data/ — 資料資源目錄

本目錄存放系統所需的靜態資料資源，包括模擬長者人設、對話測試情境以及衛教知識庫文件。這些資料供後端 Lambda、Bedrock Knowledge Base 與測試腳本使用。

## 目錄結構

```
data/
├── knowledge/       # 衛教知識庫文件（txt 純文字）
├── personas/        # 模擬長者人設定義（JSON）
├── scenarios/       # 對話測試情境腳本
├── seed.py          # DynamoDB 資料種子腳本
└── README.md
```

## 各子目錄與檔案說明

### knowledge/

存放從衛福部、國健署等公開來源整理的衛教文章（純文字格式）。這些文件會透過 `scripts/build_kb_upload.py` 轉換成 Bedrock Knowledge Base 格式（正文 + metadata sidecar），上傳至 S3 後供對話大腦的 `search_health_knowledge` 工具檢索。

每篇文件開頭固定兩行 metadata（`標題:` / `來源:`），正文從第三行開始。

涵蓋主題：

| 分類 | 文件範例 |
|------|----------|
| 慢性病管理 | `高血壓.txt`、`糖尿病.txt`、`代謝症候群.txt`、`慢性阻塞性肺病.txt`、`氣喘.txt` |
| 腦心血管 | `腦中風.txt`、`3大關鍵行動 預防中風 守護腦健康.txt`、`天冷氣溫驟降　護心4招要記住.txt` |
| 失智照護 | `失智症照護與服務資源.txt`、`失智照護服務計畫.txt`、`失智症是正常老化現象，無法避免_.txt` |
| 長照服務 | `照顧服務.txt`、`喘息服務.txt`、`交通接送服務.txt`、`輔具及居家無障礙環境改善.txt` |
| 預防保健 | `「防跌三步」守護長者安全.txt`、`活躍老化-社區長者健康動起來.txt`、`及早存骨本 5招鞏固骨骼健康.txt` |

### personas/

模擬長者的個人資料 JSON 檔案，用於本地測試與資料庫種子。

| 檔案 | 說明 |
|------|------|
| `eld_001.json` | 模擬長者「陳阿蘭」：1948 年生女性，高血壓 + 膝關節退化，喜歡公園散步與歌仔戲，兒子每週三來訪 |

欄位定義對應 `docs/api.md` 的長者資料結構（`elder_id`、`name`、`nickname`、`birth_year`、`gender`、`lang_preference`、`health_notes`、`family`、`habit_note`）。

### scenarios/

對話測試情境腳本，供系統整合測試與 Demo 使用。不同情境（如忘記吃藥、反映胸痛、一般閒聊）用以測試對話大腦在不同意圖下的回應行為。

### seed.py

DynamoDB 資料種子腳本，負責將 `personas/` 的模擬長者與範例 routines 寫入開發環境資料庫。全部使用模擬 persona，不含真實個資。

```bash
python data/seed.py
```
