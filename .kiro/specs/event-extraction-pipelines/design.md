# Design Document

## Overview

本設計把現行單一條事件擷取路徑（`extraction/pipeline.py::ExtractionPipeline`）抽成「可註冊、可由環境變數切換的整條 pipeline」，並新增三條較簡潔的策略。四條 pipeline 共用同一份尾段（temporal → canonical key → slot 去重 → 型別驗證），使重跑冪等性與跨 pipeline 可比性由單一實作保證，而非由每條 pipeline 各自遵守約定。

設計上有三個核心取捨：

1. **註冊單位是整條 pipeline，不是 stage。** stage 級抽換會逼出一組通用的 stage 介面，而四條 pipeline 的階段形狀本來就不同（有的沒有 chunk、有的沒有 concept_id）。以整條為單位，每條 pipeline 內部可以用最自然的資料流，代價是階段程式碼要靠共用函式而非共用介面來重用。
2. **尾段是唯一的寫入前收斂點。** 事件身分（`canonical_event_key`、`event_id`）與丟棄判定全部集中在 `shared_tail.py`；pipeline 只負責產出「尚未有身分的事件草稿」。這讓「同 snapshot 重跑產生相同 key 集合」變成一條可測性質，而不是四份需要分別驗證的行為。
3. **七大類 pipeline 借 pseudo concept 填 `concept_id`。** 下游（統計、摘要、`GET /events`）已經依賴 `concept_id` 與 `taxonomy_version` 存在，若新 pipeline 留空就得在每個下游加特例。以 `UCO.HighLevel.{type_id}` 這種登錄於 taxonomy 的虛擬節點承接，代價是這些事件沒有節點繼承屬性，`structured_detail` 明顯較稀疏。

實作範圍：`backend/src/extraction/`、`backend/scripts/`，加上 `handlers/batch_extractor.py` 取得 pipeline 的那一段接線。DynamoDB 寫入責任不動。

## Architecture

### 模組佈局

```
backend/src/extraction/
  results.py                  新增：LlmUsage、PipelineResult（四條 pipeline 的共同輸出型別）
  shared_tail.py              新增：SharedTail（共用尾段）、EventOrigin、TailResult
  planning.py                 新增：plan_session_chunks（rac_uco 與 chunked_seven 共用分塊）
  pipelines/
    __init__.py               匯入四條 pipeline 完成註冊，並轉出 registry 公開介面
    registry.py               新增：register / create / available / PipelineConfigError
    base.py                   新增：PipelineDeps、PipelineSpec、ExtractionPipeline（Protocol）
    seven_type.py             新增：七大類 prompt、靜態 schema 與萃取（多條 pipeline 共用）
    rac_uco.py                新增：既有 ExtractionPipeline 的註冊包裝
    direct_seven.py           新增
    chunked_seven.py          新增
    summarize_then_label.py   新增
  pipeline.py                 修改：事件身分計算改委派 SharedTail，回傳 results.PipelineResult
  taxonomy.py                 修改：載入七個 Pseudo_Concept 並提供查詢與撞號校驗
  config.py                   修改：新增 pipeline 選用與分批相關環境變數
backend/scripts/
  evaluate_extraction_pipelines.py   新增：離線多 pipeline 評測與報表
backend/src/handlers/
  batch_extractor.py          修改：改由 registry 取得 pipeline；manifest 可為 None
```

依賴方向：`pipelines/*` → `shared_tail`／`planning`／`seven_type` → 既有 stage 模組（`chunker`、`chunk_planner`、`retriever`、`classifier`、`pruner`、`schema_composer`、`extractor`、`temporal`、`canonical`、`dedup`）。`results.py` 與 `shared_tail.py` 不 import 任何 `pipelines/*`，避免循環。

### 資料流

```
handler ──ExtractionConfig.from_env()──► registry.create(config.pipeline_name, deps)
                                              │
                                              ▼
                                   ┌──────────────────────┐
frozen turns ─────────────────────►│  Extraction_Pipeline │  四條擇一
                                   └──────────┬───────────┘
                                              │ ExtractedEvent + EventOrigin（每筆草稿）
                                              ▼
                                   ┌──────────────────────┐
                                   │      SharedTail      │ 1 temporal
                                   │                      │ 2 canonical key / event_id
                                   │                      │ 3 slot 去重
                                   │                      │ 4 型別驗證
                                   └──────────┬───────────┘
                                              ▼
                                        PipelineResult
                                     （events + metrics）
                                              │
                                              ▼
                            handler：條件式 PutItem 寫入 events
```

四條 pipeline 的階段組成：

| pipeline | 分塊 | 概念檢索 | RAC 分類 | 剪枝 | 動態 schema | 萃取單位 | LLM 呼叫數（典型） |
|---|---|---|---|---|---|---|---|
| `rac_uco` | 有 | 有 | 有 | 有 | 有 | 每 chunk 一次 | 2 × chunk 數 |
| `chunked_seven` | 有 | 無 | 無 | 無 | 無（靜態七類 schema） | 每 chunk 一次 | 1 × chunk 數 |
| `direct_seven` | 無 | 無 | 無 | 無 | 無（靜態七類 schema） | 每 batch 一次 | batch 數（多數 session 為 1） |
| `summarize_then_label` | 無 | 無 | 無（改為注入清單分類） | 無 | 僅 `uco` 空間有 | 摘要 + 標籤（+ 屬性填充） | 2～3 |

## Components and Interfaces

### 1. Pipeline_Registry（`pipelines/registry.py`、`pipelines/base.py`）

`PipelineDeps` 是唯一的依賴注入容器；`PipelineSpec.requires_retriever` 讓 handler 只在必要時才建立 S3 Vectors client 與 embedder，避免簡潔 pipeline 付出不必要的初始化成本。

```python
# pipelines/base.py
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class PipelineDeps:
    """建立 pipeline 所需的全部外部依賴；缺少者以 None 表示。"""
    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    client: Any = None                      # bedrock-runtime
    retriever: ConceptRetriever | None = None
    embedder: Any = None
    segmenter: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None


@runtime_checkable
class ExtractionPipeline(Protocol):
    """一條完整的事件擷取策略。"""
    name: str

    def plan(
        self, session_id: str, session_snapshot_hash: str, turns: Sequence[Turn]
    ) -> ChunkManifest | None:
        """規劃分塊；不分塊的 pipeline 回 None，handler 便不持久化 manifest。"""

    def run(
        self,
        elder_id: str,
        session_id: str,
        session_snapshot_hash: str,
        turns: Sequence[Turn],
        *,
        manifest: ChunkManifest | None = None,
        elder: Mapping[str, Any] | None = None,
    ) -> PipelineResult: ...


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    factory: Callable[[PipelineDeps], ExtractionPipeline]
    requires_retriever: bool = False
    stage_metric_keys: tuple[str, ...] = ()   # 該 pipeline 專屬的 metrics key，供文件與測試斷言
```

```python
# pipelines/registry.py
class PipelineConfigError(ValueError):
    """pipeline 名稱未登錄；屬部署設定錯誤，不做降級。"""

_REGISTRY: dict[str, PipelineSpec] = {}

def register(name: str, *, requires_retriever: bool = False,
            stage_metric_keys: tuple[str, ...] = ()) -> Callable[[F], F]:
    """裝飾工廠函式完成註冊；同名重複註冊直接拋錯，避免匯入順序決定行為。"""

def available() -> tuple[str, ...]:
    """已登錄名稱，字典序排序（錯誤訊息與報表需要確定性順序）。"""

def spec(name: str) -> PipelineSpec:
    if name not in _REGISTRY:
        raise PipelineConfigError(
            f"未登錄的 EXTRACTION_PIPELINE：{name!r}；可用值：{'、'.join(available())}"
        )
    return _REGISTRY[name]

def create(name: str, deps: PipelineDeps) -> ExtractionPipeline:
    return spec(name).factory(deps)
```

`pipelines/__init__.py` 匯入四個模組以觸發註冊，並轉出 `create`／`available`／`spec`／`PipelineConfigError`／`PipelineDeps`。handler 只 import `src.extraction.pipelines`，不再 import 具體類別。

handler 端的改動集中在兩處：

```python
def build_pipeline(config: ExtractionConfig) -> pipelines.ExtractionPipeline:
    taxonomy = load_taxonomy(config.taxonomy_assets_dir)
    lexicon = load_predicate_lexicon(config.taxonomy_assets_dir)
    spec = pipelines.spec(config.pipeline_name)          # 名稱不合法在此就中止
    embedder = bedrock.BedrockEmbeddingProvider(...)
    retriever = _build_retriever(config, taxonomy, embedder) if spec.requires_retriever else None
    return pipelines.create(config.pipeline_name, pipelines.PipelineDeps(...))

# _run_extraction 內
planned = pipeline.plan(session_id, snapshot_hash, turns)
if planned is None:
    manifest = None                                       # 不分塊的 pipeline 不寫 manifest
else:
    ...沿用既有的條件式持久化與還原邏輯...
```

`PipelineConfigError` 在 handler 屬永久錯誤（設定問題重試無用），對應到 `PermanentBatchError`。

### 2. Shared_Tail（`shared_tail.py`）

尾段是**有狀態的累積器**：pipeline 逐筆 `absorb` 草稿（完成 temporal 與身分計算），最後 `finalize` 一次做 slot 去重與型別驗證。這個形狀讓四條 pipeline 的呼叫方式完全一致，也讓「一次 session 只做一次去重」這件事無法被漏掉。

```python
@dataclass(frozen=True)
class EventOrigin:
    """一批草稿的共同來源脈絡。"""
    reference_datetime: str                     # 取自 core range 最末 turn 的 created_at
    evidence_conversation_ids: tuple[str, ...]  # 僅 core range，脈絡 turn 不得入列
    source_chunk_id: str | None = None
    classification_confidence: float | None = None


@dataclass(frozen=True)
class TailResult:
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats
    dropped_events: int = 0
    unmatched_predicates: int = 0


@dataclass
class SharedTail:
    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    embedder: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None
    family_aliases: Mapping[str, str] = field(default_factory=dict)

    def absorb(self, *, elder_id: str, session_id: str,
               extracted: ExtractedEvent, origin: EventOrigin) -> bool:
        """步驟 1～2：時間解析 + 身分計算。回傳謂語是否命中受控詞彙。

        無法形成身分（正規化後謂語為空）者直接丟棄並累加 dropped_events。
        """

    def finalize(self) -> TailResult:
        """步驟 3～4：slot 去重（dedup.deduplicate）後做事件型別驗證。"""
```

四個步驟與既有模組的對應：

| 步驟 | 實作 | 說明 |
|---|---|---|
| 1 時間解析 | `temporal.resolve_observed_at(observed_at, raw_expr, origin.reference_datetime)` | 一律以 turn 的 `created_at` 為基準，禁用 `datetime.now()` |
| 2 canonical key | `canonical.normalize_subject` / `normalize_predicate` / `canonical_event_key(ts, subject, predicate, config.event_slot_minutes)` / `event_id_for` | 從 `pipeline.py::_build_canonical_event` 原封搬入，含決策 C 的 `suspected_routine_id` 標記與 predicate 命中標記 |
| 3 slot 去重 | `dedup.deduplicate(drafts, slot_minutes=config.event_slot_minutes, lexicon=lexicon)` | 完全沿用，四條 pipeline 共用同一份 slot 粒度 |
| 4 型別驗證 | `shared_tail._validate_event` | `concept_id` 存在於 taxonomy（含 pseudo）、`type` ∈ 七類、`ts`／`subject`／`predicate`／`detail` 非空、`structured_detail` 僅含允許屬性 |

型別驗證放在去重之後：去重會重算謂語與 key（`_rekey`），先驗證等於驗證中間狀態。單筆驗證失敗只丟該筆、`dropped_events += 1`，同批其餘事件照常輸出。

### 3. Extraction_Config（`config.py`）

```python
@dataclass(frozen=True)
class ExtractionConfig:
    ...
    pipeline_name: str = PIPELINE_RAC_UCO              # "rac_uco"
    extraction_label_space: str = LABEL_SPACE_UCO     # "uco" | "high_level" (相容 SUMMARIZE_LABEL_SPACE)
    seven_batch_char_limit: int = 12000                # 單次七類萃取的逐字稿字元上限
    summarizer_model_id: str = ""                      # 摘要階段可換便宜模型
    labeler_model_id: str = ""

    def model_for(self, stage: str) -> str | None:
        # 新增 "summarizer"、"labeler" 兩個階段，空字串仍 fallback 到 model_id
```

`from_env()` 新增讀取：

| 環境變數 | 預設 | 可選值 | 說明 |
|---|---|---|---|
| `EXTRACTION_PIPELINE` | `rac_uco` | `rac_uco`、`direct_seven`、`summarize_then_label`、`chunked_seven` | 欲執行的 pipeline |
| `EXTRACTION_LABEL_SPACE` | `uco` | `uco`、`high_level` | 三種簡潔 pipeline (direct_seven, chunked_seven, summarize_then_label) 可選七大類（含 other）或 UCO/POC |
| `SUMMARIZE_LABEL_SPACE` | `uco` | `uco`、`high_level` | 舊版相容變數（無 EXTRACTION_LABEL_SPACE 時使用） |
| `SEVEN_BATCH_CHAR_LIMIT` | `12000` | 正整數 | 逐字稿批次上限 |
| `BEDROCK_SUMMARIZER_MODEL_ID` | `""` | 模型 ID | 摘要模型 |
| `BEDROCK_LABELER_MODEL_ID` | `""` | 模型 ID | 分類標籤模型 |

沿用既有 `_env_str`／`_env_int` 語意：空字串與非法值退回預設。名稱合法性不在 config 檢查（config 不該認識 registry），改由 `registry.spec()` 在建立時拋 `PipelineConfigError`；`extraction_label_space` 非法值退回 `uco` 並記 warning。七大類 (high_level) 模式包含 `diet`、`activity`、`sleep`、`medication`、`wellbeing`、`safety`、`other`（其中一項即為 `other`）。

### 4. Pseudo_Concept 與 Taxonomy_Loader（`taxonomy.py`）

七個 pseudo concept 在載入階段合成為 level 1 的合法節點，注入 `Taxonomy.nodes` 與 `Taxonomy.mappings`：

```python
PSEUDO_CONCEPT_PREFIX = "UCO.HighLevel."

def pseudo_concept_id(type_id: str) -> str:
    return f"{PSEUDO_CONCEPT_PREFIX}{type_id}"

# _load_taxonomy_cached 內，於 mappings 校驗之前
def _build_pseudo_nodes(types, nodes) -> dict[str, ConceptNode]:
    collisions = [pseudo_concept_id(t.id) for t in types if pseudo_concept_id(t.id) in nodes]
    if collisions:
        raise TaxonomyError(f"Pseudo concept 與既有節點 id 重複：{'、'.join(collisions)}")
    return {
        pseudo_concept_id(t.id): ConceptNode(
            concept_id=pseudo_concept_id(t.id),
            display_name=t.display_name,
            display_name_en=None,
            level=1,
            is_leaf=False,          # 關鍵：不進 leaf_ids，故不觸發「葉節點缺謂語候選」校驗
            definition=t.description,
            retrieval_description="",   # 空字串代表不進概念向量索引
            parent=None,
            children=(),
            synonyms=(),
            examples=(),
            own_properties=(),      # 無繼承屬性（Req 7.5）
        )
        for t in types
    }
```

`Taxonomy` 新增查詢方法：

```python
def is_pseudo_concept(self, concept_id: str) -> bool: ...
def pseudo_concept_id(self, type_id: str) -> str:
    """type_id 必須屬於七類，否則拋 TaxonomyError。"""
def pseudo_concept_for_label(self, label: str) -> tuple[str, bool]:
    """把模型回傳的標籤映射為 (pseudo concept_id, 是否為合法標籤)；非法時回 default_type 的 pseudo。"""
```

對既有行為的影響面：

- `mappings` 加入七筆 pseudo → `high_level_type(pseudo_id)` 精確命中，不走祖先鏈也不落 default。
- `leaf_ids()` 不變（`is_leaf=False`），因此 `unmapped_leaf_ids()` 與 `canonical.validate_lexicon()` 的結果不變。
- `retriever` 由 `concept_chunks.jsonl` 建索引，資產內沒有 pseudo，因此 `rac_uco` 的候選集合不變；`scripts/build_concept_vector_index.py` 與 `dump_taxonomy.py` 若走 `taxonomy.nodes`，需以 `is_pseudo_concept` 過濾（在任務中處理）。
- `schema_composer.compose_multi_event(pseudo_id)` 會得到「只有基底欄位 + 全域屬性」的 schema，這正是 Req 7.5 描述的稀疏結果。

### 5. 七大類萃取（`pipelines/seven_type.py`）

`direct_seven`、`chunked_seven` 與 `summarize_then_label` 的 `high_level` 空間共用這一份 prompt 與 schema。schema 是**靜態**的（`Literal` 固定為七類），所以 Bedrock structured outputs 的 grammar 可以穩定命中快取，這是相對 `rac_uco` 動態 schema 的附帶好處。

```python
def build_seven_type_prompt(
    unit_id: str, transcript: str, reference_datetime: str, taxonomy: Taxonomy,
    *, elder: Mapping[str, Any] | None = None,
) -> str:
    """注入七類的 id、display_name、description，加上事件分裂原則與時序推導規則。"""

def seven_type_event_model(taxonomy: Taxonomy) -> type[BaseModel]:
    """以 Literal[七類 id] 收斂 high_level_type 的靜態事件模型；結果 lru_cache。

    欄位：event_index、high_level_type、subject、predicate、event_summary、
    raw_temporal_expression、observed_at、confidence_score。
    刻意不含 source_utterance／evidence_span（決策 D）。
    """

@dataclass(frozen=True)
class SevenTypeExtraction:
    events: tuple[ExtractedEvent, ...]      # concept_id 已填為 pseudo concept id
    dropped_events: int = 0
    unmapped_type_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)   # 含 usage、latency_ms

def extract_seven_type_events(
    unit_id: str, transcript: str, reference_datetime: str, taxonomy: Taxonomy,
    *, elder=None, extraction_mode=EXTRACTION_PROMPT_GUIDED,
    model_id: str | None = None, client=None,
) -> SevenTypeExtraction:
    """單次 LLM 呼叫萃取七類事件；沿用 extractor.py 的有界修復與逐筆容錯策略。"""
```

標籤收斂邏輯統一走 `taxonomy.pseudo_concept_for_label`：合法標籤 → 對應 pseudo；非法（含空值、幻覺類別、大小寫不符）→ `other` 的 pseudo 且 `unmapped_type_count += 1`。

`ExtractedEvent.attributes` 只放 pipeline 自產欄位（`classification_confidence` 不適用；保留 `is_novel_predicate` 等由尾段補上的標記）。

#### 依 turn 邊界分批

```python
@dataclass(frozen=True)
class TurnBatch:
    ordinal: int
    start: int          # 閉區間，frozen turns 的 0-indexed
    end: int

def plan_turn_batches(turns: Sequence[Turn], char_limit: int) -> tuple[TurnBatch, ...]:
    """貪婪累積 turn 至字元上限；單一 turn 超限時自成一批（不切開 turn）。

    保證：批次連續、互不重疊、完整覆蓋 [0, len(turns)-1]。
    """
```

字元數是 token 的代理指標：中文 token 與字元數比例穩定，且不需要引入 tokenizer 依賴。`char_limit` 由 `SEVEN_BATCH_CHAR_LIMIT` 控制。

### 6. `direct_seven`（`pipelines/direct_seven.py`）

```python
@register("direct_seven", stage_metric_keys=("direct_seven_batch_count", "unmapped_type_count"))
def build(deps: PipelineDeps) -> ExtractionPipeline:
    return DirectSevenPipeline(deps)
```

`plan()` 回 `None`。`run()`：

1. `plan_turn_batches(turns, config.seven_batch_char_limit)`。
2. 每批渲染逐字稿（`speaker：text`，無脈絡前綴，因為批次本身即連續全文），`reference_datetime` 取該批最末 turn 的 `created_at`，`evidence_conversation_ids` 取該批全部 turn id。
3. 依 `config.extraction_label_space`（`high_level` 七大類包含 `other`，或 `uco` 模式）執行萃取 → 逐筆 `tail.absorb`，`usage.record(metadata)`。
4. `tail.finalize()` 組 `PipelineResult`，`stage_metrics = {"direct_seven_batch_count": len(batches), "unmapped_type_count": n}`。

不建立 manifest，`source_chunk_id` 填 `None`（`events` 表該欄位可空）。

### 7. `chunked_seven`（`pipelines/chunked_seven.py`）

`plan()` 走 `planning.plan_session_chunks`，與 `rac_uco` 同一份實作（`chunker.plan_boundaries` + `chunk_planner.plan_chunks`），因此 `chunk_id` 的確定性與冪等性一致。

```python
def plan_session_chunks(
    config, session_id, session_snapshot_hash, turns, *, embedder=None, client=None, segmenter=None
) -> ChunkManifest:
    """既有 ExtractionPipeline.plan 的內容搬出成共用函式。

    plan_boundaries 內部已對分塊失敗做機械切分降級並標記 fallback_used；
    若 plan_chunks 自身拋 ChunkPlanError（邊界不合法），改以整個 session 為單一 chunk，
    fallback_used=True。
    """
```

`run()`：對 `manifest.chunks` 逐一 `render_chunk_text`（含 `（脈絡）` 前綴）→ 依 `config.extraction_label_space`（`high_level` 含 `other`，或 `uco`）進行萃取 → `absorb`，`evidence` 取 `core_turn_ids`、`reference` 取 `reference_datetime_for`。`stage_metrics = {"chunk_count", "chunker_fallback_used", "unmapped_type_count"}`。

### 8. `summarize_then_label`（`pipelines/summarize_then_label.py`）

三個階段，`uco` 空間才有第三階段：

```python
@dataclass(frozen=True)
class EventCandidate:
    """摘要階段產物：有內容、沒有分類。"""
    candidate_index: int
    subject: str
    predicate: str
    detail: str
    raw_temporal_expression: str | None
    observed_at: str | None
    evidence_conversation_ids: tuple[str, ...]
    reference_datetime: str

def summarize_events(batch_transcript, reference_datetime, *, elder, model_id, client) -> tuple[EventCandidate, ...]
def label_candidates(candidates, label_options, *, model_id, client) -> tuple[LabeledCandidate, ...]
def fill_structured_detail(labeled, taxonomy, *, model_id, client) -> dict[int, dict[str, Any]]
```

- **階段 1 摘要**：依 `plan_turn_batches` 分批（與 `direct_seven` 同一函式），每批一次呼叫，輸出候選事件。不注入任何分類資訊，避免摘要階段就被標籤空間帶偏。
- **階段 2 標籤**：把候選事件的 `detail`／`subject`／`predicate` 與標籤清單一起送出，一次呼叫分類多筆。
  - `label_space == "high_level"`：清單為七類（id + display_name + description），`concept_id` 直接取 pseudo。
  - `label_space == "uco"`：清單為 `taxonomy.leaf_ids()` 的 `display_name` + `label_description_for_retrieval`。這是本 pipeline 的成本熱點（清單長），但省掉整套向量索引維運。
- **階段 3 屬性填充**（僅 `uco`）：把標籤結果依 `concept_id` 分組，以 `LabelHit` 呼叫既有 `schema_composer.compose_multi_event`，再單次呼叫模型填屬性，最後走 `prune_irrelevant_event_properties` 過濾跨概念滲透。`high_level` 空間跳過此階段（pseudo concept 無繼承屬性）。

非法標籤（不在注入清單內）→ `other` 的 pseudo + `unmapped_type_count += 1`。

`stage_metrics = {"summarize_call_count", "label_call_count", "property_fill_call_count"（僅 uco）, "label_space", "candidate_count", "unmapped_type_count"}`。

### 9. `rac_uco`（`pipelines/rac_uco.py`）

薄包裝，行為不變：

```python
@register("rac_uco", requires_retriever=True,
          stage_metric_keys=("chunk_count", "chunker_fallback_used", "hit_count",
                             "candidate_count", "structured_output_degraded"))
def build(deps: PipelineDeps) -> ExtractionPipeline:
    if deps.retriever is None:
        raise PipelineConfigError("rac_uco 需要 ConceptRetriever")
    return ExtractionPipeline(config=..., taxonomy=..., retriever=deps.retriever, ...)
```

`pipeline.py` 的內部改動只有兩處：`_build_canonical_event` 的內容移入 `SharedTail`（呼叫端改為 `tail.absorb`），`run()` 改回傳 `results.PipelineResult` 並把 `chunk_count`／`chunker_fallback_used`／`structured_output_degraded` 等放進 `stage_metrics`。既有 metrics key 集合維持不變（見下節）。

### 10. Pipeline_Metrics（`results.py`）

```python
@dataclass
class LlmUsage:
    """LLM 呼叫記帳；可變累積器，pipeline 每次呼叫後 record。"""
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    usage_missing_count: int = 0
    structured_output_degraded: int = 0

    def record(self, metadata: Mapping[str, Any]) -> None:
        """從 bedrock.converse 的 metadata 取 usage.inputTokens／outputTokens。

        缺 usage 或欄位為 None 時 token 記 0 並累加 usage_missing_count（Req 8.6）。
        """


@dataclass(frozen=True)
class PipelineResult:
    session_id: str
    pipeline_name: str
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats = field(default_factory=DedupStats)
    usage: LlmUsage = field(default_factory=LlmUsage)
    manifest: ChunkManifest | None = None
    dropped_events: int = 0
    unmatched_predicates: int = 0
    stage_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def metrics(self) -> dict[str, Any]:
        """共用 key + 該 pipeline 的階段 key；階段 key 不覆寫共用 key。"""
```

共用 key（四條 pipeline 都有）：

| key | 來源 |
|---|---|
| `pipeline_name` | 註冊名稱 |
| `event_count` | `len(events)` |
| `dropped_events`、`unmatched_predicates` | 尾段 |
| `dedup_merge_rate`、`dedup_key_merged`、`dedup_alias_merged` | `DedupStats` |
| `llm_call_count`、`llm_input_tokens`、`llm_output_tokens`、`llm_usage_missing_count` | `LlmUsage` |
| `model_latency_ms` | `LlmUsage.latency_ms` |
| `type_distribution` | 七個 High_Level_Type id 為 key，未出現者為 0 |

階段 key 由各 pipeline 的 `stage_metrics` 提供，未執行的階段不輸出對應 key（Req 8.5）。`rac_uco` 的階段 key 涵蓋 `chunk_count`、`chunker_fallback_used`、`structured_output_degraded`，因此 Req 6.3 列舉的 11 個既有 key 全數保留。

`shared/metrics.py::emit_pipeline_metrics` 需要一筆 additive 修改才能在 CloudWatch 上分 pipeline 比較：加入 `PipelineName` 維度與 `LlmCallCount`／`LlmInputTokens`／`LlmOutputTokens`／`LlmUsageMissing` 四個指標名稱。既有指標名稱與維度不變，因此不影響現有儀表板。這是本功能唯一觸及 `src/shared/` 的改動。

### 11. Evaluation_Script（`scripts/evaluate_extraction_pipelines.py`）

```
python scripts/evaluate_extraction_pipelines.py \
    --sessions data/eval/sessions.txt \
    --pipelines rac_uco,direct_seven,summarize_then_label,chunked_seven \
    --gold data/eval/gold_annotations.json \
    --out results/extraction/pipeline_report.json
```

參數：`--sessions`（逗號清單或每行一筆的檔案，格式 `elder_id:session_id`）、`--pipelines`、`--gold`、`--out`，另有 `--label-space`、`--limit`、`--dry-run` 輔助選項。

執行流程：

1. 載入並驗證 Gold_Annotation（缺欄位或非法標籤 → `SystemExit`，列出所有 session id／標籤值）。
2. 讀取 session 與 frozen turns（`sessions.get_frozen_turns`、`db.get_elder`），**只讀**。
3. 對每個 session、每條 pipeline：以 `dataclasses.replace(base_config, pipeline_name=name)` 建 config，`registry.create` 建 pipeline，計時執行 `run()`。單一組合拋例外 → 記入 `failed_sessions`，繼續其餘組合。
4. 計算報表並寫入 `--out`，同時印出比較表。

Gold_Annotation 格式（JSON 陣列或 JSONL）：

```json
[
  {
    "session_id": "ses_...",
    "elder_id": "eld_...",
    "annotator": "chen",
    "annotated_at": "2026-07-26T21:00:00+08:00",
    "events": [
      {"detail": "晚餐吃了半碗飯", "high_level_type": "diet"},
      {"detail": "睡前量血壓 135/85", "high_level_type": "wellbeing"}
    ]
  }
]
```

品質指標採**型別計數比對（count-based matching）**，不做語意配對，也不引入 LLM 評審（避免模型自產自審）：

```python
def type_scores(gold_counts: Mapping[str, int], pred_counts: Mapping[str, int]) -> dict[str, dict[str, float]]:
    """對每個 High_Level_Type：tp = min(pred, gold)、fp = pred - tp、fn = gold - tp。

    precision = tp / (tp + fp)，recall = tp / (tp + fn)，f1 = 調和平均；
    分母為 0 時該值定義為 0.0。macro_f1 = 七類 f1 的算術平均。
    """
```

報表結構：

```python
{
  "generated_at": "...",
  "sessions": {"total": 12, "annotated": 10, "unannotated": ["ses_x", "ses_y"]},
  "pipelines": {
    "direct_seven": {
      "quality": {"per_type": {"diet": {"precision":…, "recall":…, "f1":…}, …}, "macro_f1": …},
      "granularity": {"event_total": …, "events_per_session": …, "detail_avg_chars": …},
      "identity": {"canonical_key_duplicate_rate": …},
      "cost": {"llm_call_count": …, "llm_input_tokens": …, "llm_output_tokens": …},
      "latency": {"p50_ms": …, "p95_ms": …}
    }, …
  },
  "best_by_metric": {"macro_f1": "rac_uco", "llm_input_tokens": "direct_seven", …},
  "failed_sessions": [{"session_id": "…", "pipeline": "…", "error": "…"}]
}
```

`best_by_metric` 依每個指標宣告的方向取極值（`macro_f1`、per-type F1 越高越好；`llm_*`、`p50_ms`、`p95_ms`、`canonical_key_duplicate_rate` 越低越好）。並列時取字典序最小的名稱，保證報表可重現。

未標註 session：不計入品質指標分母，仍計入成本與延遲，並列於 `sessions.unannotated`。

腳本只寫 `--out` 指定的檔案；不讀寫 `.env`、`terraform.tfvars` 或任何設定檔，生產選用由人工依報表決定。

## Data Models

新增或調整的型別集中如下（既有 `CanonicalEvent`、`ExtractedEvent`、`DedupStats`、`ComposedSchema`、`ChunkManifest` 不改欄位）：

| 型別 | 模組 | 用途 |
|---|---|---|
| `PipelineDeps` | `pipelines/base.py` | 依賴注入容器 |
| `PipelineSpec` | `pipelines/base.py` | 註冊項（工廠、是否需要 retriever、階段 key） |
| `ExtractionPipeline`（Protocol） | `pipelines/base.py` | pipeline 介面契約 |
| `EventOrigin` | `shared_tail.py` | 一批草稿的共同來源脈絡 |
| `TailResult` | `shared_tail.py` | 尾段輸出 |
| `LlmUsage` | `results.py` | LLM 呼叫記帳累積器 |
| `PipelineResult` | `results.py` | 四條 pipeline 的共同輸出（取代 `pipeline.py` 內的同名型別，並在 `pipeline.py` 轉出以維持既有 import） |
| `TurnBatch` | `pipelines/seven_type.py` | 依 turn 邊界的批次範圍 |
| `SevenTypeExtraction` | `pipelines/seven_type.py` | 七類萃取輸出 |
| `EventCandidate`／`LabeledCandidate` | `pipelines/summarize_then_label.py` | 摘要與標籤階段的中間產物 |
| `GoldSession`／`GoldEvent` | `scripts/evaluate_extraction_pipelines.py` | Gold_Annotation 載入結果 |

`CanonicalEvent.to_event_item()` 的欄位集合完全不變，`GET /events` 與 `EventType` 值域因此不受影響。

## Error Handling

| 情境 | 處置 | 理由 |
|---|---|---|
| `EXTRACTION_PIPELINE` 未登錄 | `PipelineConfigError` → handler 轉 `PermanentBatchError` | 設定錯誤重試無用；訊息列出所有可用名稱以便修正 |
| `SUMMARIZE_LABEL_SPACE` 非法 | 退回 `uco` + warning | 不影響 pipeline 是否存在，降級可用比中止服務好 |
| Pseudo concept 與既有節點撞號 | 載入階段 `TaxonomyError` | 部署期就該擋下，與既有「葉節點未映射」同一策略 |
| 模型回傳非七類標籤 | 收斂為 `other` 的 pseudo + `unmapped_type_count` | 事件內容仍有價值；以指標暴露 prompt 品質問題 |
| 單筆事件驗證失敗（含謂語為空） | 丟棄該筆 + `dropped_events` | 沿用決策 I，不讓單筆壞資料使整個 session failed |
| 整份 JSON 無法解析 | `RetryableBedrockError` | 沿用既有策略，交回 SQS 重投 |
| chunk planner 失敗 | 整個 session 視為單一 chunk，`chunker_fallback_used=True` | 分塊是品質優化而非正確性前提 |
| 評測腳本單一 (session, pipeline) 失敗 | 記入 `failed_sessions`，繼續其餘組合 | 一次跑數十組合，單點失敗不該讓整份報表作廢 |
| Gold 缺 `annotator`／`annotated_at`，或標籤非七類 | `SystemExit` 並列出所有問題項 | 基準檔不可信時算出的比較結果更危險 |

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: 註冊表往返

*For any* 已登錄的 pipeline 名稱，`registry.create` 回傳的物件其 `name` 等於該名稱、可接受同一批 frozen turns 執行，且回傳 `PipelineResult` 的 `metrics["pipeline_name"]` 等於該名稱。

**Validates: Requirements 1.1, 1.5, 8.1**

### Property 2: 未登錄名稱一律拋設定錯誤且訊息完整

*For any* 不在註冊表內的非空字串，取得 pipeline 的操作拋出 `PipelineConfigError`，且錯誤訊息包含 `registry.available()` 的每一個名稱。

**Validates: Requirements 1.4**

### Property 3: 環境變數解析

*For any* 字串（含純空白與未設定），`ExtractionConfig.from_env()` 的 `pipeline_name` 等於該字串去除前後空白後的值，空值時等於 `rac_uco`；`summarize_label_space` 於值屬於 `{uco, high_level}` 時等於該值，否則等於 `uco`。

**Validates: Requirements 1.2, 1.3, 4.3, 6.1**

### Property 4: 尾段身分計算

*For any* 事件草稿與任意合法的 `EVENT_SLOT_MINUTES`，尾段輸出事件的 `canonical_event_key` 等於以正規化後的 subject、predicate、解析後的 `ts` 與同一 slot 粒度獨立重算的結果，`event_id` 等於 `event_id_for(elder_id, canonical_event_key)`，且 `to_event_item()` 的欄位集合恆等於既有欄位集合。

**Validates: Requirements 2.1, 2.3, 2.5**

### Property 5: 尾段重跑冪等

*For any* 事件草稿序列，以同一組設定執行尾段兩次，產出的事件序列（canonical key 集合與排序）完全相同。

**Validates: Requirements 2.4**

### Property 6: 型別驗證只丟壞事件

*For any* 合法與不合法草稿的混合序列，尾段輸出恰為所有合法草稿對應的事件，且 `dropped_events` 等於不合法草稿的數量。

**Validates: Requirements 2.6, 2.7**

### Property 7: 所有 pipeline 都經共用尾段

*For any* 已登錄的 pipeline 與任意模型回應，其輸出的每個事件都能以尾段函式對同一輸入重算出相同的 `canonical_event_key`、`event_id` 與 `ts`。

**Validates: Requirements 2.2**

### Property 8: 七類標籤收斂與 pseudo concept 對應

*For any* 只產七大類標籤的 pipeline 與任意模型回傳的標籤字串（含幻覺與空值），每個輸出事件的 `type` 屬於七個 High_Level_Type id、`concept_id` 等於該 type 的 pseudo concept id、`taxonomy_version` 等於現行 taxonomy 版本戳記，且非法標籤的筆數等於 `unmapped_type_count`。

**Validates: Requirements 3.4, 3.5, 4.4, 4.6, 7.3**

### Property 9: Pseudo concept 往返與載入校驗

*For any* High_Level_Type id，`high_level_type(pseudo_concept_id(id))` 回到原 id、該 pseudo concept 不出現在 `leaf_ids()`、其繼承屬性集合為空；*for any* 與 pseudo id 撞號的資產，載入階段拋出 `TaxonomyError` 且訊息包含所有重複的 id。

**Validates: Requirements 7.1, 7.2, 7.4**

### Property 10: 事件必備欄位與證據來源

*For any* pipeline 的任意輸出事件，`detail` 非空、`evidence_conversation_ids` 非空且為該萃取單位 core range turn id 的子集，且事件的欄位與 `structured_detail` 皆不包含 `context_snippet`、`evidence_span`、`rationale`。

**Validates: Requirements 3.6, 4.1, 6.6**

### Property 11: structured_detail 屬性白名單

*For any* 輸出事件（含屬性滲透的模型回應），`structured_detail` 的 key 皆落在該 `concept_id` 的允許屬性集合聯集 pipeline 自產標記欄位內；當 `concept_id` 為 pseudo concept 時，允許屬性集合為空，故僅剩 pipeline 自產標記欄位。

**Validates: Requirements 4.5, 7.5**

### Property 12: 七類標籤注入內容一致

*For any* taxonomy 資產，`direct_seven` 與 `chunked_seven` 的萃取 prompt 皆包含七個 High_Level_Type 的 id、display_name 與 description，且兩者使用同一份標籤注入區塊與同一個事件輸出 schema。

**Validates: Requirements 3.3, 5.3**

### Property 13: 簡潔 pipeline 不觸發檢索、分類與資料庫寫入

*For any* frozen turns，`direct_seven`、`chunked_seven` 與 `summarize_then_label` 的執行都不呼叫概念向量檢索與 RAC 分類；*for any* 已登錄 pipeline 的執行，皆不呼叫任何 DynamoDB 寫入操作。

**Validates: Requirements 3.2, 4.2, 6.4**

### Property 14: 萃取單位完整覆蓋與呼叫數會計

*For any* frozen turns 與任意字元上限，`plan_turn_batches` 產生的批次連續、互不重疊且完整覆蓋所有 turn 索引，`direct_seven_batch_count` 等於批次數；*for any* frozen turns，`chunked_seven` 的 `chunk_count` 等於 manifest 的 chunk 數且 LLM 呼叫次數等於 chunk 數。

**Validates: Requirements 3.1, 3.7, 5.2, 5.4**

### Property 15: 分塊失敗降級仍產出事件

*For any* frozen turns，當分塊規劃拋出例外時，`chunked_seven` 仍以整個 session 為單一 chunk 完成執行，`chunk_count` 為 1、`chunker_fallback_used` 為 true，且事件輸出不因降級而變空。

**Validates: Requirements 5.5**

### Property 16: metrics 契約與階段 key

*For any* 已登錄 pipeline 的執行結果，`metrics` 的 key 集合等於共用 key 集合聯集該 pipeline 宣告的階段 key 集合；`type_distribution` 的 key 恆為七個 High_Level_Type id 且各值等於該 type 的事件筆數；`rac_uco` 的 `metrics` 包含本功能實作前既有的 11 個 key。

**Validates: Requirements 6.3, 7.7, 8.3, 8.4, 8.5**

### Property 17: LLM 記帳不變式

*For any* LLM 呼叫序列（含缺少 usage 的回應），`llm_call_count` 等於實際呼叫次數、`llm_input_tokens` 與 `llm_output_tokens` 等於各次回報值之和（缺少者計 0）、`llm_usage_missing_count` 等於缺少 usage 的次數；*for any* `summarize_then_label` 執行，各階段呼叫次數之和等於 `llm_call_count`。

**Validates: Requirements 4.7, 8.2, 8.6**

### Property 18: 報表分類指標定義

*For any* gold 與預測的型別計數組合，per-type precision、recall、F1 皆落在 [0, 1]；兩者計數完全相同時所有非零型別的 F1 為 1；`macro_f1` 等於七類 F1 的算術平均。

**Validates: Requirements 9.3**

### Property 19: 報表統計指標與最佳標記

*For any* 事件集合與延遲樣本，`event_total` 等於各 session 事件數之和、`events_per_session` 等於總數除以 session 數、`canonical_key_duplicate_rate` 等於 (事件數 − 相異 canonical key 數) / 事件數、`p50_ms` 不大於 `p95_ms` 且兩者落在樣本值域內；*for any* 指標矩陣，`best_by_metric` 標記的 pipeline 為該指標依其方向的極值持有者。

**Validates: Requirements 9.4, 9.5, 9.6, 9.7**

### Property 20: 評測組合完整性與容錯

*For any* session 清單、pipeline 清單與任意失敗組合子集，報表包含所有非失敗組合的結果、`failed_sessions` 恰為失敗組合且各含失敗原因；未提供 Gold_Annotation 的 session 出現在未標註清單、仍有成本與延遲指標、且不計入品質指標。

**Validates: Requirements 9.2, 9.9, 10.6**

### Property 21: Gold_Annotation 載入往返與驗證

*For any* 合法 Gold_Annotation 結構，序列化後再載入得到等價結果；*for any* 缺少 `annotator` 或 `annotated_at` 的 session 子集，載入中止且訊息包含所有缺欄位的 session id；*for any* 不屬於七類的標籤值，載入中止且訊息包含該標籤值。

**Validates: Requirements 10.1, 10.2, 10.3, 10.4**

## Testing Strategy

### 測試層級與檔案

| 檔案 | 涵蓋 |
|---|---|
| `tests/test_extraction_pipeline_registry.py` | 註冊表往返、未登錄名稱錯誤、config 解析、handler 只走 registry |
| `tests/test_extraction_shared_tail.py` | 身分計算、冪等重跑、型別驗證丟棄、決策 D 禁用欄位 |
| `tests/test_extraction_pseudo_concepts.py` | pseudo concept 往返、撞號拋錯、`leaf_ids` 與 lexicon 校驗不受影響 |
| `tests/test_extraction_direct_seven.py` | 分批覆蓋、標籤收斂、不觸發檢索、metrics 階段 key |
| `tests/test_extraction_chunked_seven.py` | chunk 數與呼叫數會計、分塊降級、與 `direct_seven` 的 prompt 一致性 |
| `tests/test_extraction_summarize_then_label.py` | 兩／三階段呼叫計數、標籤空間切換、`structured_detail` 白名單 |
| `tests/test_extraction_metrics_contract.py` | metrics key 集合、`type_distribution` 七 key、LLM 記帳（含 usage 缺失） |
| `tests/test_evaluate_pipelines.py` | 分類指標、統計指標、best 標記、失敗容錯、gold 驗證與往返 |
| 既有 `tests/test_extraction_pipeline.py`、`tests/test_batch_extractor.py` | `rac_uco` 行為不變的回歸基準，必須維持全綠 |

### 雙軌測試

- **單元測試**：具體案例與邊界（`CHUNKER_TYPE` 個別值、gold 檔的具體錯誤訊息、handler 的 `PipelineConfigError` → `PermanentBatchError` 轉換、`rac_uco` 對固定假回應的 golden 事件輸出）。
- **性質測試**：上節 21 條性質，以 `hypothesis` 實作，每條至少 100 次迭代。需在 `pyproject.toml` 的 `[project.optional-dependencies].dev` 加入 `hypothesis`（僅開發期依賴，不進 Lambda 部署包）。

性質測試標註格式：

```python
@given(...)
@settings(max_examples=100)
def test_shared_tail_rerun_is_idempotent(drafts):
    """Feature: event-extraction-pipelines, Property 5: For any 事件草稿序列，
    以同一組設定執行尾段兩次，產出的事件序列完全相同。"""
```

### 生成器與替身

- `Turn` 生成器：任意 speaker／text／遞增 `created_at`（時間必須單調，否則 `reference_datetime` 語意不成立）。
- 模型回應生成器：以 `FakeConverseClient` 餵入 JSON 字串，涵蓋合法標籤、幻覺標籤、缺欄位、屬性滲透、缺 `usage` 五類。
- `taxonomy` 與 `lexicon` 一律用真實資產（`load_taxonomy()`／`load_predicate_lexicon()`），撞號測試才用臨時資產目錄。
- 檢索與資料庫替身：被呼叫即 `raise AssertionError`，讓「不該呼叫」變成可失敗的斷言而非人工檢查。
- 評測腳本測試以 stub pipeline（不呼叫模型）與 stub 資料層執行，全程離線。

### 不用性質測試的部分

- `docs/` 文件內容（Req 6.5、7.6、11.x）：以一個 lint 型測試檢查 `config.py` 讀取的環境變數名稱都出現在 `docs/framework.md`，其餘靠人工檢視決策表。
- `PipelineSpec` 公開介面形狀（Req 1.6）、腳本只寫 `--out`（Req 9.10）、gold 檔唯讀（Req 10.5）：各一個 smoke／example 測試即可，行為不隨輸入變化。
- 真實 Bedrock 呼叫與 DynamoDB 讀取：不進單元測試；評測腳本的線上執行由人工在 `--limit` 小樣本下驗證。
