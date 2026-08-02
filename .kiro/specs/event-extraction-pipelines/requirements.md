# Requirements Document

## Introduction

事件擷取（時間軸）模組目前只有單一條 pipeline：分塊 → 概念檢索（S3 Vectors）→ RAC 分類 → HMLC 剪枝 → 動態 schema 組裝 → 單次萃取 → 共用尾段。這條路徑品質最高但成本、延遲與實作複雜度也最高，且沒有對照組可以證明每個階段的貢獻。

本功能把「整條 pipeline」抽成可註冊、可由環境變數切換的策略，並新增三條較簡潔的 pipeline：`direct_seven`（整個 session 一次抽七大類事件）、`summarize_then_label`（先事件摘要再直覺標籤分類）、`chunked_seven`（沿用現有分塊但不做檢索與 RAC，作為隔離「分塊」變因的對照組）。現行實作保留為 `rac_uco` 基線。所有 pipeline 共用同一份尾段（temporal → canonical key → slot 去重 → 型別驗證），以保住重跑冪等性與跨 pipeline 可比性。

同時交付離線評測腳本：對同一批 session 跑多條 pipeline，依人工標註的 gold session 產出七大類 P/R/F1、事件粒度、canonical key 重複率與成本延遲報表，作為勝出判定依據。

範圍限定於 `backend/src/extraction/` 與 `backend/scripts/`。對外 API 契約（`GET /events` 欄位與 `EventType` 值域、`daily_summaries.sections` 的七個 key）不在本功能變更範圍內；DynamoDB 寫入仍是 `handlers/batch_extractor.py` 的責任。

## Glossary

- **Extraction_Pipeline**：一條完整的事件擷取策略，輸入為某個 session 的 frozen turns，輸出為 `PipelineResult`（`CanonicalEvent` 清單與 metrics）。
- **Pipeline_Registry**：`backend/src/extraction/` 內以名稱對應 Extraction_Pipeline 建構函式的註冊表，唯一的 pipeline 取得入口。
- **Shared_Tail**：所有 Extraction_Pipeline 必須執行的共用尾段，依序為時間解析（temporal）、canonical key 計算、slot 去重、事件型別驗證，介面邊界為 `CanonicalEvent`。
- **Rac_Uco_Pipeline**：註冊名稱 `rac_uco`，等同本功能實作前的 `pipeline.py::ExtractionPipeline` 行為。
- **Direct_Seven_Pipeline**：註冊名稱 `direct_seven`，不分塊、不檢索，對整個 session 一次抽出七大類事件。
- **Summarize_Then_Label_Pipeline**：註冊名稱 `summarize_then_label`，先產生事件摘要，再以注入的標籤名稱清單分類，不使用向量檢索。
- **High_Level_Type**：`high_level_types.json`（version `high-level-types-1.0.0`）定義的七大類 id：`diet`、`activity`、`sleep`、`medication`、`wellbeing`、`safety`、`other`（其中一項即為 `other`），`default_type` 為 `other`，順序即摘要呈現順序。
- **Pseudo_Concept**：代表某個 High_Level_Type 的虛擬分類節點，concept_id 形如 `UCO.HighLevel.diet`，登錄於 taxonomy 資產但不具備繼承屬性。
- **Taxonomy_Loader**：`taxonomy.py` 內載入並校驗 taxonomy 資產的元件。
- **Extraction_Config**：`config.py::ExtractionConfig`，frozen dataclass 加 `from_env()`，本功能新增的環境變數一律由此讀取。
- **Pipeline_Metrics**：`PipelineResult.metrics` 字典。
- **Evaluation_Script**：`backend/scripts/` 內的離線評測腳本，對同一批 session 執行多條 Extraction_Pipeline 並輸出比較報表。
- **Gold_Annotation**：人工標註的評測基準檔，含每個 session 的事件清單與 High_Level_Type 標籤，並含標註者識別欄位。
- **Documentation_Set**：`docs/framework.md`、`docs/feature_events-extraction.md`、`docs/api.md`。

## Requirements

### Requirement 1：Pipeline 註冊表與環境變數選用

**User Story:** As a 後端開發者, I want 以單一環境變數切換整條事件擷取 pipeline, so that 我能在生產環境安全地試用較簡潔的策略而不改程式碼

#### Acceptance Criteria

1. THE Pipeline_Registry SHALL 以名稱註冊 `rac_uco`、`direct_seven`、`summarize_then_label`、`chunked_seven` 四條 Extraction_Pipeline。
2. THE Extraction_Config SHALL 由環境變數 `EXTRACTION_PIPELINE` 讀取 pipeline 名稱，預設值為 `rac_uco`。
3. WHEN 環境變數 `EXTRACTION_PIPELINE` 的值為空字串或未設定，THE Extraction_Config SHALL 使用 `rac_uco`。
4. IF 環境變數 `EXTRACTION_PIPELINE` 的值為非空字串且未登錄於 Pipeline_Registry，THEN THE Pipeline_Registry SHALL 拋出設定錯誤，並在錯誤訊息中列出所有已登錄名稱。
5. WHEN 呼叫端向 Pipeline_Registry 請求某個名稱的 Extraction_Pipeline，THE Pipeline_Registry SHALL 回傳一個接受 frozen turns 清單並回傳 `PipelineResult` 的物件。
6. THE Pipeline_Registry SHALL 以 pipeline 為註冊單位，不提供 stage 級的個別替換介面。
7. WHERE 呼叫端為 `handlers/batch_extractor.py`，THE Pipeline_Registry SHALL 為唯一取得 Extraction_Pipeline 的途徑。

### Requirement 2：共用尾段強制執行

**User Story:** As a 後端開發者, I want 所有 pipeline 都跑同一份尾段, so that 重跑結果冪等且不同 pipeline 的輸出可以直接比較

#### Acceptance Criteria

1. THE Shared_Tail SHALL 依序執行時間解析、canonical key 計算、slot 去重、事件型別驗證四個步驟。
2. WHEN 任一 Extraction_Pipeline 產生事件，THE Extraction_Pipeline SHALL 將該事件交給 Shared_Tail 處理後才放入 `PipelineResult`。
3. THE Shared_Tail SHALL 以 `CanonicalEvent` 為輸出型別，並保留 `to_event_item()` 既有欄位集合。
4. WHEN 同一個 session 的同一批 frozen turns 以同一條 Extraction_Pipeline 執行兩次且模型輸出相同，THE Shared_Tail SHALL 產生相同的 canonical key 集合。
5. THE Shared_Tail SHALL 使用 `EVENT_SLOT_MINUTES` 定義的 slot 粒度計算 canonical key，四條 pipeline 共用同一份計算邏輯。
6. IF 單一事件未通過型別驗證，THEN THE Shared_Tail SHALL 丟棄該事件、保留同批其餘事件，並將 `dropped_events` 計數加一。
7. THE Shared_Tail SHALL 在 `PipelineResult.metrics` 回報 `dedup_merge_rate`、`dedup_key_merged`、`dedup_alias_merged`、`dropped_events`。

### Requirement 3：`direct_seven` pipeline

**User Story:** As a 後端開發者, I want 一條單次呼叫抽事件的 pipeline, so that 我能用最低成本與延遲取得可用的時間軸

#### Acceptance Criteria

1. WHEN Direct_Seven_Pipeline 處理一個 session，THE Direct_Seven_Pipeline SHALL 以該 session 的完整 frozen turns 作為單一萃取單位，不呼叫 chunk planner。
2. THE Direct_Seven_Pipeline SHALL 不執行概念向量檢索，也不執行 RAC 分類。
3. THE Direct_Seven_Pipeline SHALL 支援以 `EXTRACTION_LABEL_SPACE` 設定標籤空間（`high_level` 或 `uco`）；在 `high_level` 空間時在 prompt 中注入七個 High_Level_Type（含 `other`）的 id、display_name 與 description。
4. WHEN Direct_Seven_Pipeline 產生事件，THE Direct_Seven_Pipeline SHALL 為每個事件填入對應類別標籤（`high_level` 模式下填入七大類包含 `other` 的 `high_level_type` 與對應 Pseudo_Concept；`uco` 模式下填入 UCO concept_id 及其繼承屬性）。
5. IF 模型在 `high_level` 模式回傳的類別不屬於七個 High_Level_Type id，THEN THE Direct_Seven_Pipeline SHALL 將該事件的 `high_level_type` 設為 `other`，並將 `unmapped_type_count` 計數加一。
6. THE Direct_Seven_Pipeline SHALL 為每個事件填入 `detail` 與 `evidence_conversation_ids`。
7. WHERE session 的 frozen turns 長度超出單次萃取的 token 上限，THE Direct_Seven_Pipeline SHALL 依 turn 邊界切為多個連續批次送出，並將 `direct_seven_batch_count` 記入 Pipeline_Metrics。

### Requirement 4：`summarize_then_label` pipeline

**User Story:** As a 後端開發者, I want 先摘要事件再直覺標籤分類的 pipeline, so that 我能在不維運向量索引的前提下取得結構化事件

#### Acceptance Criteria

1. WHEN Summarize_Then_Label_Pipeline 處理一個 session，THE Summarize_Then_Label_Pipeline SHALL 先執行多事件摘要階段，為 session 中每個候選事件產出 `detail`、時間線索與 `evidence_conversation_ids`。
2. THE Summarize_Then_Label_Pipeline SHALL 在標籤階段以 prompt 注入的標籤名稱清單進行分類，不執行概念向量檢索。
3. THE Extraction_Config SHALL 由環境變數 `EXTRACTION_LABEL_SPACE`（與 `SUMMARIZE_LABEL_SPACE` 相容）讀取標籤空間，適用於三種 pipeline（`direct_seven`、`chunked_seven`、`summarize_then_label`），可選值為 `high_level` 與 `uco`，預設值為 `uco`。
4. WHERE 標籤空間為 `high_level`，THE Summarize_Then_Label_Pipeline SHALL 注入包含 `other` 在內的七個 High_Level_Type 並將事件對應到 Pseudo_Concept。
5. WHERE 標籤空間為 `uco`，THE Summarize_Then_Label_Pipeline SHALL 注入 UCO 分類節點的名稱與描述清單，並在分類完成後注入該節點的繼承屬性以產生 `structured_detail`。
6. IF 標籤階段回傳的標籤不在注入清單內，THEN THE Summarize_Then_Label_Pipeline SHALL 將該事件對應到 `other` 的 Pseudo_Concept，並將 `unmapped_type_count` 計數加一。
7. THE Summarize_Then_Label_Pipeline SHALL 在 Pipeline_Metrics 回報摘要階段與標籤階段各自的 LLM 呼叫次數。

### Requirement 5：`chunked_seven` 對照組 pipeline

**User Story:** As a 後端開發者, I want 一條只保留分塊、移除檢索與分類的對照組, so that 我能量化「分塊」與「檢索加分類」各自對品質的貢獻

#### Acceptance Criteria

1. WHEN Chunked_Seven_Pipeline 處理一個 session，THE Chunked_Seven_Pipeline SHALL 使用 `CHUNKER_TYPE` 指定的既有 chunk planner 產生 chunk。
2. THE Chunked_Seven_Pipeline SHALL 對每個 chunk 各執行一次事件萃取，且不執行概念向量檢索與 RAC 分類。
3. THE Chunked_Seven_Pipeline SHALL 支援可配置標籤空間（`high_level` 或 `uco`）；在 `high_level` 模式下使用包含 `other` 在內的七大類標籤與對應 Pseudo_Concept，在 `uco` 模式下使用 UCO 空間。
4. WHEN Chunked_Seven_Pipeline 完成一個 session，THE Chunked_Seven_Pipeline SHALL 在 Pipeline_Metrics 回報 `chunk_count`。
5. IF chunk planner 執行失敗，THEN THE Chunked_Seven_Pipeline SHALL 改以整個 session 作為單一 chunk 繼續處理，並將 `chunker_fallback_used` 設為 true。

### Requirement 6：`rac_uco` 基線與向後相容

**User Story:** As a 產品維運者, I want 未設定新環境變數時系統行為與現在完全一致, so that 這次重構不影響已上線的時間軸與摘要

#### Acceptance Criteria

1. WHEN 環境變數 `EXTRACTION_PIPELINE` 未設定，THE Pipeline_Registry SHALL 執行 Rac_Uco_Pipeline。
2. THE Rac_Uco_Pipeline SHALL 保留分塊、概念檢索、RAC 分類、HMLC 剪枝、動態 schema 組裝與單次萃取六個階段的既有行為。
3. WHILE 執行 Rac_Uco_Pipeline，THE Pipeline_Metrics SHALL 包含本功能實作前既有的所有 key：`chunk_count`、`event_count`、`dropped_events`、`unmatched_predicates`、`dedup_merge_rate`、`dedup_key_merged`、`dedup_alias_merged`、`chunker_fallback_used`、`structured_output_degraded`、`model_latency_ms`、`type_distribution`。
4. THE Extraction_Pipeline SHALL 對所有註冊名稱都不執行 DynamoDB 寫入，寫入責任留在 `handlers/batch_extractor.py`。
5. THE Documentation_Set SHALL 保持 `docs/api.md` 的 `GET /events` 回應欄位與 `EventType` 值域不變。
6. WHEN 任一 Extraction_Pipeline 產生事件，THE Extraction_Pipeline SHALL 沿用既有決策 C（疑似 routine 只在 `structured_detail` 標記 `suspected_routine_id`）與決策 D（僅落地 `evidence_conversation_ids`，不落地 `context_snippet`、`evidence_span`、`rationale`）。

### Requirement 7：七大類 Pseudo_Concept 與 taxonomy 資產登錄

**User Story:** As a 後端開發者, I want 只產七大類標籤的 pipeline 也能填出合法的 concept_id, so that 下游統計與摘要不需要為新 pipeline 特例處理

#### Acceptance Criteria

1. THE Taxonomy_Loader SHALL 為七個 High_Level_Type 各載入一個 Pseudo_Concept，concept_id 格式為 `UCO.HighLevel.{type_id}`。
2. THE Taxonomy_Loader SHALL 使每個 Pseudo_Concept 的 `high_level_type` 等於其對應的 High_Level_Type id。
3. WHEN 某條 Extraction_Pipeline 只產出七大類標籤，THE Extraction_Pipeline SHALL 將事件的 `concept_id` 設為對應的 Pseudo_Concept id，並將 `taxonomy_version` 設為現行 taxonomy 版本戳記。
4. IF 某個 Pseudo_Concept id 與既有 UCO 分類節點 id 重複，THEN THE Taxonomy_Loader SHALL 在載入階段拋出錯誤並列出重複的 id。
5. THE Pseudo_Concept SHALL 不提供繼承屬性，因此對應事件的 `structured_detail` 僅包含 pipeline 自行產生的欄位。
6. THE Documentation_Set SHALL 記錄「Pseudo_Concept 事件缺少節點繼承屬性、`structured_detail` 較稀疏」這項權衡。
7. WHEN 下游依類別彙總事件，THE Extraction_Pipeline SHALL 使 `high_level_type` 足以完成彙總，`daily_summaries.sections` 的七個 key 對應關係維持不變。

### Requirement 8：可觀測性 metrics

**User Story:** As a 產品維運者, I want metrics 能辨識當次執行用了哪條 pipeline 與花了多少成本, so that 我能在 CloudWatch 上比較不同 pipeline 的實際表現

#### Acceptance Criteria

1. WHEN 任一 Extraction_Pipeline 完成一個 session，THE Pipeline_Metrics SHALL 包含 `pipeline_name`，其值等於該 pipeline 的註冊名稱。
2. THE Pipeline_Metrics SHALL 包含 `llm_call_count`、`llm_input_tokens`、`llm_output_tokens`。
3. THE Pipeline_Metrics SHALL 包含 `model_latency_ms` 與 `event_count`。
4. THE Pipeline_Metrics SHALL 包含 `type_distribution`，以七個 High_Level_Type id 為 key。
5. WHERE pipeline 具有專屬階段，THE Pipeline_Metrics SHALL 額外包含該階段的計數 key，未執行的階段不輸出對應 key。
6. IF 某個 LLM 呼叫未回傳 token 使用量，THEN THE Extraction_Pipeline SHALL 將該次呼叫的 token 計為 0 並將 `llm_usage_missing_count` 計數加一。

### Requirement 9：離線評測腳本與量化報表

**User Story:** As a 後端開發者, I want 一個能對同一批 session 跑多條 pipeline 並出報表的腳本, so that 勝出判定有量化依據而不是憑感覺

#### Acceptance Criteria

1. THE Evaluation_Script SHALL 接受 session 識別清單、待評測的 pipeline 名稱清單、Gold_Annotation 檔路徑與輸出報表路徑四項參數。
2. WHEN 使用者提供多個 pipeline 名稱，THE Evaluation_Script SHALL 對每個 session 依序執行每一條指定的 pipeline，並在單一報表中並列各 pipeline 的結果。
3. THE Evaluation_Script SHALL 在報表中對每條 pipeline 輸出七大類的 per-type precision、recall、F1 與 macro F1。
4. THE Evaluation_Script SHALL 在報表中對每條 pipeline 輸出事件粒度指標：事件總數、每 session 平均事件數、`detail` 平均字數。
5. THE Evaluation_Script SHALL 在報表中對每條 pipeline 輸出 canonical key 重複率。
6. THE Evaluation_Script SHALL 在報表中對每條 pipeline 輸出成本與延遲指標：`llm_call_count`、`llm_input_tokens`、`llm_output_tokens`、每 session 延遲的 p50 與 p95。
7. THE Evaluation_Script SHALL 對每個量化指標標示表現最佳的 pipeline 名稱。
8. THE Evaluation_Script SHALL 只讀取 session 與 turn 資料，並將結果寫入輸出報表檔案。
9. IF 某個 session 在某條 pipeline 執行時拋出例外，THEN THE Evaluation_Script SHALL 記錄該 session 與 pipeline 的失敗原因、繼續執行其餘組合，並在報表中以 `failed_sessions` 呈現失敗清單。
10. THE Evaluation_Script SHALL 不修改任何環境變數設定檔，pipeline 的生產選用由人工決定。

### Requirement 10：Gold_Annotation 標註來源

**User Story:** As a 專案負責人, I want 評測基準由人工標註, so that 比較結果不是模型自產自審

#### Acceptance Criteria

1. THE Gold_Annotation SHALL 對每個 session 記錄事件清單，每個事件含 `detail` 與一個 High_Level_Type 標籤。
2. THE Gold_Annotation SHALL 對每個 session 記錄 `annotator` 與 `annotated_at` 欄位。
3. IF Gold_Annotation 檔中任一 session 缺少 `annotator` 或 `annotated_at`，THEN THE Evaluation_Script SHALL 中止執行並回報缺少欄位的 session 識別碼。
4. IF Gold_Annotation 檔中任一事件的標籤不屬於七個 High_Level_Type id，THEN THE Evaluation_Script SHALL 中止執行並回報該標籤值。
5. THE Evaluation_Script SHALL 不產生也不修改 Gold_Annotation 內容。
6. WHERE 待評測 session 不存在對應的 Gold_Annotation，THE Evaluation_Script SHALL 略過該 session 的品質指標、仍輸出其成本與延遲指標，並在報表中標示為未標註。

### Requirement 11：文件同步

**User Story:** As a 團隊成員, I want 文件與實作同步, so that 我能只讀文件就知道有哪些 pipeline 與環境變數

#### Acceptance Criteria

1. THE Documentation_Set SHALL 在 `docs/framework.md` 的後端環境變數章節登錄 `EXTRACTION_PIPELINE` 與 `SUMMARIZE_LABEL_SPACE`，含可選值與預設值。
2. THE Documentation_Set SHALL 在 `docs/framework.md` 的 Repo 結構章節列出新增的 pipeline 模組與 Evaluation_Script 檔案。
3. THE Documentation_Set SHALL 在 `docs/feature_events-extraction.md` 的決策表新增 pipeline registry 決策、共用尾段決策與 Pseudo_Concept 權衡三筆記錄。
4. THE Documentation_Set SHALL 在 `docs/feature_events-extraction.md` 記錄四條 pipeline 各自的階段組成與適用情境。
