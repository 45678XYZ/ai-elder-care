# Implementation Plan: event-extraction-pipelines

## Overview

實作語言為 Python（沿用 `backend/` 既有結構與 `pytest`＋`hypothesis` 測試）。

執行順序刻意先做「共用基礎」再做「各條 pipeline」：`results.py`（共同輸出型別與 LLM 記帳）→ `shared_tail.py`（唯一的寫入前收斂點）→ taxonomy pseudo concept → `config.py` → `pipelines/registry.py`＋`base.py` → `planning.py`。基礎完成後，先把既有 `rac_uco` 遷到新型別（回歸基準），再依 `seven_type` → `direct_seven` → `chunked_seven` → `summarize_then_label` 順序疊加，最後才接 metrics、handler、評測腳本與文件。

每條 pipeline 都不做 DynamoDB 寫入；寫入責任留在 `handlers/batch_extractor.py`。

## Tasks

- [x] 1. 共同輸出型別與 LLM 記帳基礎
  - [x] 1.1 加入 `hypothesis` 開發期依賴
    - 在 `backend/pyproject.toml` 的 `[project.optional-dependencies].dev` 加入 `hypothesis`
    - 確認 `requirements.txt`（Lambda 部署包）不受影響
    - _Requirements: 8.2_

  - [x] 1.2 新增 `backend/src/extraction/results.py`
    - 實作 `LlmUsage`（可變累積器：`call_count`、`input_tokens`、`output_tokens`、`latency_ms`、`usage_missing_count`、`structured_output_degraded`）與 `record(metadata)`，缺 usage 或欄位為 None 時 token 記 0 並累加 `usage_missing_count`
    - 實作 `PipelineResult`（frozen dataclass）與 `metrics` property：共用 key 聯集 `stage_metrics`，階段 key 不覆寫共用 key
    - `type_distribution` 固定以七個 High_Level_Type id 為 key，未出現者為 0
    - 不 import 任何 `pipelines/*`，避免循環依賴
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 2.7_

  - [x] 1.3 撰寫 LLM 記帳性質測試
    - 檔案：`backend/tests/test_extraction_metrics_contract.py`
    - **Property 17: LLM 記帳不變式**
    - **Validates: Requirements 4.7, 8.2, 8.6**

- [x] 2. 共用尾段 Shared_Tail
  - [x] 2.1 新增 `backend/src/extraction/shared_tail.py` 的型別與 `absorb`
    - 定義 `EventOrigin`（`reference_datetime`、`evidence_conversation_ids`、`source_chunk_id`、`classification_confidence`）與 `TailResult`
    - `SharedTail.absorb()`：步驟 1 時間解析（`temporal.resolve_observed_at`，一律以 turn `created_at` 為基準，禁用 `datetime.now()`）＋步驟 2 身分計算（`canonical.normalize_subject`／`normalize_predicate`／`canonical_event_key`／`event_id_for`）
    - 由 `pipeline.py::_build_canonical_event` 原封搬入，含決策 C 的 `suspected_routine_id` 標記與 predicate 命中標記；正規化後謂語為空者丟棄並累加 `dropped_events`
    - _Requirements: 2.1, 2.5, 6.6_

  - [x] 2.2 實作 `SharedTail.finalize()`
    - 步驟 3 slot 去重（`dedup.deduplicate`，slot 粒度取 `config.event_slot_minutes`）
    - 步驟 4 型別驗證 `_validate_event`：`concept_id` 存在於 taxonomy（含 pseudo）、`type` ∈ 七類、`ts`／`subject`／`predicate`／`detail` 非空、`structured_detail` 僅含允許屬性
    - 驗證置於去重之後；單筆失敗只丟該筆並 `dropped_events += 1`，同批其餘事件照常輸出
    - 回傳 `TailResult`，供 `PipelineResult` 產出 `dedup_merge_rate`、`dedup_key_merged`、`dedup_alias_merged`、`dropped_events`、`unmatched_predicates`
    - _Requirements: 2.1, 2.3, 2.6, 2.7_

  - [x] 2.3 撰寫尾段身分計算性質測試
    - 檔案：`backend/tests/test_extraction_shared_tail.py`
    - **Property 4: 尾段身分計算**
    - **Validates: Requirements 2.1, 2.3, 2.5**

  - [x] 2.4 撰寫尾段冪等性質測試
    - **Property 5: 尾段重跑冪等**
    - **Validates: Requirements 2.4**

  - [x] 2.5 撰寫型別驗證性質測試
    - **Property 6: 型別驗證只丟壞事件**
    - **Validates: Requirements 2.6, 2.7**

  - [x] 2.6 撰寫尾段單元測試
    - 決策 D：輸出事件與 `structured_detail` 不含 `context_snippet`、`evidence_span`、`rationale`
    - 空謂語草稿被丟棄、`to_event_item()` 欄位集合與既有一致
    - _Requirements: 2.3, 6.6_

- [x] 3. 七大類 Pseudo_Concept 與 Taxonomy_Loader
  - [x] 3.1 在 `backend/src/extraction/taxonomy.py` 合成 pseudo concept 節點
    - 新增 `PSEUDO_CONCEPT_PREFIX` 與 `pseudo_concept_id(type_id)`（`UCO.HighLevel.{type_id}`）
    - `_build_pseudo_nodes`：七類各建一個 level 1、`is_leaf=False`、`own_properties=()`、`retrieval_description=""` 的 `ConceptNode`，注入 `Taxonomy.nodes` 與 `Taxonomy.mappings`
    - 於 mappings 校驗前檢查與既有節點 id 撞號，撞號拋 `TaxonomyError` 並列出所有重複 id
    - _Requirements: 7.1, 7.2, 7.4, 7.5_

  - [x] 3.2 新增 `Taxonomy` 的 pseudo concept 查詢方法
    - `is_pseudo_concept(concept_id)`、`pseudo_concept_id(type_id)`（非七類拋 `TaxonomyError`）、`pseudo_concept_for_label(label)` 回傳 `(concept_id, 是否合法)`，非法標籤回 `default_type`（`other`）的 pseudo
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 3.3 過濾 pseudo concept 於既有腳本
    - `backend/scripts/build_concept_vector_index.py` 與 `backend/scripts/dump_taxonomy.py` 走 `taxonomy.nodes` 之處以 `is_pseudo_concept` 排除，維持概念向量索引與匯出內容不變
    - _Requirements: 6.2, 7.1_

  - [x] 3.4 撰寫 pseudo concept 性質測試
    - 檔案：`backend/tests/test_extraction_pseudo_concepts.py`
    - **Property 9: Pseudo concept 往返與載入校驗**
    - **Validates: Requirements 7.1, 7.2, 7.4**

  - [x] 3.5 撰寫 taxonomy 回歸單元測試
    - `leaf_ids()`、`unmapped_leaf_ids()` 與 `canonical.validate_lexicon()` 結果不受 pseudo concept 影響
    - `schema_composer.compose_multi_event(pseudo_id)` 只得到基底欄位與全域屬性
    - _Requirements: 7.5, 6.2_

- [x] 4. Extraction_Config 擴充
  - [x] 4.1 在 `backend/src/extraction/config.py` 新增設定欄位與環境變數讀取
    - 新增 `pipeline_name`（預設 `rac_uco`）、`extraction_label_space`（預設 `uco`，可選 `high_level` 與 `uco`；相容舊版 `SUMMARIZE_LABEL_SPACE`）、`seven_batch_char_limit`（預設 12000）、`summarizer_model_id`、`labeler_model_id`
    - `from_env()` 讀取 `EXTRACTION_PIPELINE`、`EXTRACTION_LABEL_SPACE`（與 `SUMMARIZE_LABEL_SPACE`）、`SEVEN_BATCH_CHAR_LIMIT`、`BEDROCK_SUMMARIZER_MODEL_ID`、`BEDROCK_LABELER_MODEL_ID`，沿用 `_env_str`／`_env_int` 語意（空字串與非法值退回預設）
    - `extraction_label_space` 非法值退回 `uco` 並記 warning；七大類（high_level）明確包含 `other` 類別
    - `model_for(stage)` 支援 `"summarizer"`、`"labeler"`，空字串 fallback 到 `model_id`
    - _Requirements: 1.2, 1.3, 4.3, 3.7_

  - [x] 4.2 撰寫環境變數解析性質測試
    - 檔案：`backend/tests/test_extraction_config.py`
    - **Property 3: 環境變數解析**
    - **Validates: Requirements 1.2, 1.3, 4.3, 6.1**

- [x] 5. Pipeline_Registry 與 pipeline 介面
  - [x] 5.1 新增 `backend/src/extraction/pipelines/base.py`
    - `PipelineDeps`（config、taxonomy、lexicon、client、retriever、embedder、segmenter、suspected_routine_lookup）
    - `ExtractionPipeline` Protocol：`name`、`plan()`（不分塊回 `None`）、`run()` 回傳 `PipelineResult`
    - `PipelineSpec`（`name`、`factory`、`requires_retriever`、`stage_metric_keys`）
    - _Requirements: 1.5, 1.6_

  - [x] 5.2 新增 `backend/src/extraction/pipelines/registry.py`
    - `PipelineConfigError(ValueError)`、`register()` 裝飾器（同名重複註冊直接拋錯）、`available()`（字典序）、`spec()`、`create()`
    - `spec()` 對未登錄名稱拋 `PipelineConfigError`，訊息列出 `available()` 的全部名稱
    - 只以整條 pipeline 為註冊單位，不提供 stage 級替換介面
    - _Requirements: 1.1, 1.4, 1.5, 1.6_

  - [x] 5.3 新增 `backend/src/extraction/pipelines/__init__.py`
    - 匯入四個 pipeline 模組以觸發註冊（先以既有者為主，後續任務逐步補齊）
    - 轉出 `create`、`available`、`spec`、`PipelineConfigError`、`PipelineDeps`、`ExtractionPipeline`
    - _Requirements: 1.1, 1.7_

  - [x] 5.4 撰寫未登錄名稱性質測試
    - 檔案：`backend/tests/test_extraction_pipeline_registry.py`
    - **Property 2: 未登錄名稱一律拋設定錯誤且訊息完整**
    - **Validates: Requirements 1.4**

  - [x] 5.5 撰寫註冊表公開介面 smoke 測試
    - 斷言公開介面只有 pipeline 級的 `register`／`create`／`spec`／`available`，無 stage 級替換入口
    - _Requirements: 1.6_

- [x] 6. 共用分塊 planning
  - [x] 6.1 新增 `backend/src/extraction/planning.py`
    - 由既有 `ExtractionPipeline.plan` 搬出 `plan_session_chunks()`（`chunker.plan_boundaries` + `chunk_planner.plan_chunks`）
    - `plan_chunks` 拋 `ChunkPlanError` 時改以整個 session 為單一 chunk，`fallback_used=True`
    - 供 `rac_uco` 與 `chunked_seven` 共用，保證 `chunk_id` 確定性一致
    - _Requirements: 5.1, 5.5, 6.2_

  - [x] 6.2 撰寫分塊共用函式單元測試
    - 檔案：`backend/tests/test_extraction_chunking.py`
    - 同一 snapshot 重跑產生相同 `chunk_id`；`ChunkPlanError` 降級為單一 chunk 且 `fallback_used=True`
    - _Requirements: 5.1, 5.5_

- [ ] 7. Checkpoint - 共用基礎完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. `rac_uco` 遷移至新型別（回歸基準）
  - [x] 8.1 修改 `backend/src/extraction/pipeline.py`
    - `_build_canonical_event` 內容改委派 `SharedTail.absorb`／`finalize`
    - `plan()` 改呼叫 `planning.plan_session_chunks`
    - `run()` 回傳 `results.PipelineResult`，並在 `pipeline.py` 轉出 `PipelineResult` 以維持既有 import
    - `chunk_count`、`chunker_fallback_used`、`structured_output_degraded`、`hit_count`、`candidate_count` 放入 `stage_metrics`
    - _Requirements: 2.2, 6.2, 6.3, 6.4_

  - [x] 8.2 新增 `backend/src/extraction/pipelines/rac_uco.py`
    - 以 `@register("rac_uco", requires_retriever=True, stage_metric_keys=(...))` 包裝既有 `ExtractionPipeline`
    - `deps.retriever is None` 時拋 `PipelineConfigError`
    - _Requirements: 1.1, 6.1, 6.2_

  - [x] 8.3 維持 `rac_uco` 回歸測試全綠
    - 檔案：`backend/tests/test_extraction_pipeline.py`
    - 既有測試不改期望值仍全數通過；補一個斷言檢查 metrics 含實作前既有的 11 個 key（`chunk_count`、`event_count`、`dropped_events`、`unmatched_predicates`、`dedup_merge_rate`、`dedup_key_merged`、`dedup_alias_merged`、`chunker_fallback_used`、`structured_output_degraded`、`model_latency_ms`、`type_distribution`）
    - 對固定假回應的 golden 事件輸出不變
    - _Requirements: 6.2, 6.3, 6.6_

- [x] 9. 七大類共用萃取 seven_type
  - [x] 9.1 新增 `backend/src/extraction/pipelines/seven_type.py` 的 prompt 與靜態 schema
    - `build_seven_type_prompt()`：注入七類的 id、display_name、description，加上事件分裂原則與時序推導規則
    - `seven_type_event_model()`：`Literal[七類 id]` 收斂 `high_level_type` 的靜態事件模型（`event_index`、`high_level_type`、`subject`、`predicate`、`event_summary`、`raw_temporal_expression`、`observed_at`、`confidence_score`），結果 `lru_cache`；刻意不含 `source_utterance`／`evidence_span`
    - _Requirements: 3.3, 3.6, 5.3, 6.6_

  - [x] 9.2 實作 `extract_seven_type_events()` 與 `SevenTypeExtraction`
    - 單次 LLM 呼叫萃取七類事件，沿用 `extractor.py` 的有界修復與逐筆容錯策略
    - 標籤收斂統一走 `taxonomy.pseudo_concept_for_label`：非法（含空值、幻覺、大小寫不符）→ `other` 的 pseudo 且 `unmapped_type_count += 1`
    - `concept_id` 填 pseudo concept id、`taxonomy_version` 填現行版本戳記
    - `metadata` 回報 usage 與 `latency_ms`
    - _Requirements: 3.4, 3.5, 7.3, 8.2, 8.6_

  - [x] 9.3 實作 `plan_turn_batches()` 與 `TurnBatch`
    - 貪婪累積 turn 至 `config.seven_batch_char_limit`；單一 turn 超限時自成一批（不切開 turn）
    - 保證批次連續、互不重疊、完整覆蓋 `[0, len(turns)-1]`
    - _Requirements: 3.1, 3.7_

  - [x] 9.4 撰寫 seven_type 單元測試
    - 檔案：`backend/tests/test_extraction_seven_type.py`
    - prompt 含七類三項欄位；schema 的 `high_level_type` 為 `Literal` 七類；幻覺／空標籤收斂為 `other`
    - _Requirements: 3.3, 3.5_

- [x] 10. `direct_seven` pipeline
  - [x] 10.1 新增 `backend/src/extraction/pipelines/direct_seven.py`
    - `@register("direct_seven", stage_metric_keys=("direct_seven_batch_count", "unmapped_type_count"))`
    - `plan()` 回 `None`（不建立 manifest、`source_chunk_id` 為 `None`）
    - `run()`：`plan_turn_batches` → 每批渲染逐字稿（`speaker：text`）→ `extract_seven_type_events` → 逐筆 `tail.absorb` → `usage.record` → `tail.finalize`
    - `reference_datetime` 取該批最末 turn 的 `created_at`，`evidence_conversation_ids` 取該批全部 turn id
    - 不呼叫 chunk planner、不做概念向量檢索、不做 RAC 分類
    - `stage_metrics` 含 `direct_seven_batch_count`、`unmapped_type_count`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 6.4_

  - [x] 10.2 撰寫七類標籤收斂性質測試
    - 檔案：`backend/tests/test_extraction_direct_seven.py`
    - **Property 8: 七類標籤收斂與 pseudo concept 對應**
    - **Validates: Requirements 3.4, 3.5, 4.4, 4.6, 7.3**

  - [x] 10.3 撰寫事件必備欄位與證據來源性質測試
    - **Property 10: 事件必備欄位與證據來源**
    - **Validates: Requirements 3.6, 4.1, 6.6**

- [ ] 11. `chunked_seven` 對照組 pipeline
  - [x] 11.1 新增 `backend/src/extraction/pipelines/chunked_seven.py`
    - `@register("chunked_seven", stage_metric_keys=("chunk_count", "chunker_fallback_used", "unmapped_type_count"))`
    - `plan()` 走 `planning.plan_session_chunks`（沿用 `CHUNKER_TYPE`）
    - `run()`：對每個 chunk `render_chunk_text`（含「（脈絡）」前綴）→ `extract_seven_type_events` → `absorb`；`evidence` 取 `core_turn_ids`、`reference` 取 `reference_datetime_for`
    - 使用與 `direct_seven` 相同的標籤注入區塊與輸出 schema；不做檢索與 RAC 分類
    - 分塊失敗時以整個 session 為單一 chunk 繼續，`chunker_fallback_used=True`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.4_

  - [x] 11.2 撰寫七類標籤注入一致性性質測試
    - 檔案：`backend/tests/test_extraction_chunked_seven.py`
    - **Property 12: 七類標籤注入內容一致**
    - **Validates: Requirements 3.3, 5.3**

  - [ ] 11.3 撰寫萃取單位覆蓋與呼叫數會計性質測試
    - **Property 14: 萃取單位完整覆蓋與呼叫數會計**
    - **Validates: Requirements 3.1, 3.7, 5.2, 5.4**

  - [ ] 11.4 撰寫分塊降級性質測試
    - **Property 15: 分塊失敗降級仍產出事件**
    - **Validates: Requirements 5.5**

- [ ] 12. Checkpoint - 七類系列 pipeline 完成
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. `summarize_then_label` pipeline
  - [x] 13.1 實作摘要階段
    - 新增 `backend/src/extraction/pipelines/summarize_then_label.py`，定義 `EventCandidate`
    - `summarize_events()`：依 `plan_turn_batches` 分批，每批一次呼叫，輸出 `subject`／`predicate`／`detail`／時間線索／`evidence_conversation_ids`
    - 不注入任何分類資訊，避免摘要階段被標籤空間帶偏
    - _Requirements: 4.1_

  - [x] 13.2 實作標籤階段
    - `label_candidates()`：一次呼叫分類多筆，prompt 注入標籤名稱清單，不做概念向量檢索
    - `label_space == "high_level"`：注入七類（id + display_name + description），`concept_id` 取 pseudo
    - `label_space == "uco"`：注入 `taxonomy.leaf_ids()` 的 `display_name` 與 `label_description_for_retrieval`
    - 非法標籤 → `other` 的 pseudo 且 `unmapped_type_count += 1`
    - _Requirements: 4.2, 4.3, 4.4, 4.6_

  - [x] 13.3 實作屬性填充階段（僅 `uco`）
    - `fill_structured_detail()`：依 `concept_id` 分組，以 `LabelHit` 呼叫 `schema_composer.compose_multi_event`，單次呼叫模型填屬性，再走 `prune_irrelevant_event_properties` 過濾跨概念滲透
    - `high_level` 空間跳過此階段（pseudo concept 無繼承屬性）
    - _Requirements: 4.5, 7.5_

  - [ ] 13.4 組裝 pipeline 與 stage_metrics
    - `@register("summarize_then_label", stage_metric_keys=(...))`，`plan()` 回 `None`
    - 串接三階段 → `tail.absorb`／`finalize`，每次呼叫 `usage.record`
    - `stage_metrics` 含 `summarize_call_count`、`label_call_count`、`property_fill_call_count`（僅 `uco`）、`label_space`、`candidate_count`、`unmapped_type_count`
    - _Requirements: 4.7, 8.2, 8.5, 6.4_

  - [x] 13.5 撰寫 structured_detail 白名單性質測試
    - 檔案：`backend/tests/test_extraction_summarize_then_label.py`
    - **Property 11: structured_detail 屬性白名單**
    - **Validates: Requirements 4.5, 7.5**

  - [x] 13.6 撰寫標籤空間與階段計數單元測試
    - `uco` 為三階段、`high_level` 為兩階段；各階段呼叫次數之和等於 `llm_call_count`
    - `high_level` 空間不出現 `property_fill_call_count`
    - _Requirements: 4.3, 4.4, 4.7, 8.5_

- [x] 14. Pipeline_Metrics 契約與可觀測性
  - [x] 14.1 擴充 `backend/src/shared/metrics.py::emit_pipeline_metrics`
    - additive 加入 `PipelineName` 維度與 `LlmCallCount`／`LlmInputTokens`／`LlmOutputTokens`／`LlmUsageMissing` 四個指標
    - 既有指標名稱與維度不變
    - _Requirements: 8.1, 8.2_

  - [x] 14.2 撰寫 metrics 契約性質測試
    - 檔案：`backend/tests/test_extraction_metrics_contract.py`
    - **Property 16: metrics 契約與階段 key**
    - **Validates: Requirements 6.3, 7.7, 8.3, 8.4, 8.5**

  - [x] 14.3 撰寫註冊表往返性質測試
    - 檔案：`backend/tests/test_extraction_pipeline_registry.py`
    - **Property 1: 註冊表往返**
    - **Validates: Requirements 1.1, 1.5, 8.1**

  - [x] 14.4 撰寫簡潔 pipeline 不觸發檢索與寫入性質測試
    - 檢索與 DynamoDB 替身被呼叫即 `raise AssertionError`
    - **Property 13: 簡潔 pipeline 不觸發檢索、分類與資料庫寫入**
    - **Validates: Requirements 3.2, 4.2, 6.4**

  - [x] 14.5 撰寫所有 pipeline 皆經共用尾段性質測試
    - 檔案：`backend/tests/test_extraction_shared_tail.py`
    - **Property 7: 所有 pipeline 都經共用尾段**
    - **Validates: Requirements 2.2**

- [x] 15. handler 接線
  - [x] 15.1 改寫 `backend/src/handlers/batch_extractor.py::build_pipeline`
    - 只 import `src.extraction.pipelines`，以 `pipelines.spec(config.pipeline_name)` 驗名稱、`pipelines.create` 建立實例
    - 依 `spec.requires_retriever` 決定是否建立 S3 Vectors client 與 embedder
    - registry 為唯一取得 pipeline 的途徑
    - _Requirements: 1.7, 6.1_

  - [x] 15.2 調整 `_run_extraction` 的 manifest 分支與錯誤映射
    - `pipeline.plan()` 回 `None` 時不持久化 manifest；非 `None` 沿用既有條件式持久化與還原邏輯
    - `PipelineConfigError` 映射為 `PermanentBatchError`
    - DynamoDB 寫入責任維持在 handler
    - _Requirements: 1.4, 6.4, 3.1_

  - [x] 15.3 撰寫 handler 單元測試
    - 檔案：`backend/tests/test_batch_extractor.py`
    - 既有測試全綠；補測未登錄名稱 → `PermanentBatchError`、不分塊 pipeline 不寫 manifest
    - _Requirements: 1.4, 1.7, 6.4_

- [x] 16. Checkpoint - 四條 pipeline 與接線完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. 離線評測腳本
  - [x] 17.1 實作 Gold_Annotation 載入與驗證
    - 新增 `backend/scripts/evaluate_extraction_pipelines.py`，定義 `GoldSession`／`GoldEvent`
    - 支援 JSON 陣列與 JSONL；缺 `annotator`／`annotated_at` 或標籤非七類 → `SystemExit` 並列出所有問題 session id／標籤值
    - 不產生也不修改 gold 內容
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 17.2 實作報表指標計算
    - `type_scores()`：per-type `tp = min(pred, gold)`、`fp`、`fn`，precision／recall／F1（分母為 0 時定義為 0.0）、`macro_f1` 為七類算術平均
    - 粒度指標（`event_total`、`events_per_session`、`detail_avg_chars`）、`canonical_key_duplicate_rate`、成本（`llm_*`）與延遲（`p50_ms`、`p95_ms`）
    - `best_by_metric`：依各指標方向取極值，並列時取字典序最小名稱
    - _Requirements: 9.3, 9.4, 9.5, 9.6, 9.7_

  - [x] 17.3 實作 CLI 與執行流程
    - 參數 `--sessions`（逗號清單或每行 `elder_id:session_id` 的檔案）、`--pipelines`、`--gold`、`--out`，輔助選項 `--label-space`、`--limit`、`--dry-run`
    - 只讀 session 與 turn 資料（`sessions.get_frozen_turns`、`db.get_elder`）
    - 每組合以 `dataclasses.replace(base_config, pipeline_name=name)` 建 config 後計時執行；單一組合例外記入 `failed_sessions` 並繼續
    - 未標註 session 不計入品質指標、仍計成本與延遲、列於 `sessions.unannotated`
    - 只寫 `--out`，不讀寫 `.env`、`terraform.tfvars` 或任何設定檔
    - _Requirements: 9.1, 9.2, 9.8, 9.9, 9.10, 10.6_

  - [x] 17.4 撰寫 Gold_Annotation 載入性質測試
    - 檔案：`backend/tests/test_evaluate_pipelines.py`
    - **Property 21: Gold_Annotation 載入往返與驗證**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

  - [x] 17.5 撰寫分類指標定義性質測試
    - **Property 18: 報表分類指標定義**
    - **Validates: Requirements 9.3**

  - [x] 17.6 撰寫統計指標與最佳標記性質測試
    - **Property 19: 報表統計指標與最佳標記**
    - **Validates: Requirements 9.4, 9.5, 9.6, 9.7**

  - [x] 17.7 撰寫評測組合完整性與容錯性質測試
    - 以 stub pipeline 與 stub 資料層全程離線執行
    - **Property 20: 評測組合完整性與容錯**
    - **Validates: Requirements 9.2, 9.9, 10.6**

  - [x] 17.8 撰寫腳本副作用邊界單元測試
    - 執行後只有 `--out` 檔案被寫入，gold 檔內容位元不變
    - _Requirements: 9.8, 9.10, 10.5_

- [x] 18. 文件同步
  - [x] 18.1 更新 `docs/framework.md`
    - 後端環境變數章節登錄 `EXTRACTION_PIPELINE`、`SUMMARIZE_LABEL_SPACE`（含可選值與預設值），並補 `SEVEN_BATCH_CHAR_LIMIT`、`BEDROCK_SUMMARIZER_MODEL_ID`、`BEDROCK_LABELER_MODEL_ID`
    - Repo 結構章節列出 `results.py`、`shared_tail.py`、`planning.py`、`pipelines/`（含六個模組）與 `scripts/evaluate_extraction_pipelines.py`
    - _Requirements: 11.1, 11.2_

  - [x] 18.2 更新 `docs/feature_events-extraction.md`
    - 決策表新增三筆：pipeline registry（註冊單位為整條）、共用尾段為唯一收斂點、Pseudo_Concept 權衡（缺節點繼承屬性、`structured_detail` 較稀疏）
    - 記錄四條 pipeline 各自的階段組成與適用情境
    - 說明 `docs/api.md` 的 `GET /events` 欄位與 `EventType` 值域不變、`daily_summaries.sections` 七個 key 對應不變
    - _Requirements: 7.6, 11.3, 11.4, 6.5, 7.7_

  - [x] 18.3 撰寫環境變數文件 lint 測試
    - 檔案：`backend/tests/test_docs_env_contract.py`
    - 檢查 `config.py` 讀取的環境變數名稱皆出現在 `docs/framework.md`
    - _Requirements: 11.1_

- [x] 19. Final checkpoint - 全套測試與回歸
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 標記 `*` 的子任務為測試，可為 MVP 跳過；但 `8.3`（`rac_uco` 回歸）強烈建議執行，因為它是「向後相容」的唯一保護網。
- 性質測試以 `hypothesis` 實作，每條至少 `max_examples=100`，docstring 需標註 `Feature: event-extraction-pipelines, Property N`。
- `taxonomy` 與 `lexicon` 一律使用真實資產；只有 pseudo concept 撞號測試使用臨時資產目錄。
- 檢索與 DynamoDB 替身被呼叫即 `raise AssertionError`，讓「不該呼叫」成為可失敗的斷言。
- 任務 1～6 為共用基礎，任務 8 之後才動各條 pipeline；此順序確保四條 pipeline 從第一天就共用同一份尾段與 metrics 契約。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "3.1", "4.1", "5.1"] },
    { "id": 1, "tasks": ["1.3", "2.1", "3.2", "5.2", "6.1"] },
    { "id": 2, "tasks": ["2.2", "3.3", "3.4", "4.2", "5.3", "6.2"] },
    { "id": 3, "tasks": ["2.3", "3.5", "5.4", "8.1"] },
    { "id": 4, "tasks": ["2.4", "5.5", "8.2", "9.1"] },
    { "id": 5, "tasks": ["2.5", "8.3", "9.2"] },
    { "id": 6, "tasks": ["2.6", "9.3"] },
    { "id": 7, "tasks": ["9.4", "10.1", "13.1"] },
    { "id": 8, "tasks": ["10.2", "11.1", "13.2"] },
    { "id": 9, "tasks": ["10.3", "11.2", "13.3"] },
    { "id": 10, "tasks": ["11.3", "13.4", "14.1"] },
    { "id": 11, "tasks": ["11.4", "13.5", "15.1"] },
    { "id": 12, "tasks": ["13.6", "14.2", "15.2"] },
    { "id": 13, "tasks": ["14.3", "15.3", "17.1"] },
    { "id": 14, "tasks": ["14.4", "17.2"] },
    { "id": 15, "tasks": ["14.5", "17.3"] },
    { "id": 16, "tasks": ["17.4", "18.1"] },
    { "id": 17, "tasks": ["17.5", "18.2"] },
    { "id": 18, "tasks": ["17.6", "18.3"] },
    { "id": 19, "tasks": ["17.7"] },
    { "id": 20, "tasks": ["17.8"] }
  ]
}
```
