"""萃取 pipeline 的執行期設定。

所有可調行為一律由環境變數驅動，程式不寫死分類字串、模型 ID 或門檻；
變數清單與語意見 docs/framework.md 的「後端環境變數」章節。

設定物件為不可變 dataclass，於 Lambda handler 進入點建立一次後往下傳，
避免各模組各自讀 env 而在同一次執行中取到不一致的值。
"""

from dataclasses import dataclass, field
from pathlib import Path
import os

# 資產目錄預設隨部署包一起發佈，因此以本檔案位置推導而非工作目錄
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TAXONOMY_ASSETS_DIR = ASSETS_DIR / "taxonomy"
RETRIEVAL_ASSETS_DIR = ASSETS_DIR / "retrieval"
SEGMENTER_ASSETS_DIR = ASSETS_DIR / "segmenter"

# 分塊策略；llm_prompt 為預設（品質最好），encoder 系列供離線與降級使用，full_session 為整會話單一塊
CHUNKER_LLM_PROMPT = "llm_prompt"
CHUNKER_EMBEDDING_DEPTH = "embedding_depth"
CHUNKER_PAIRWISE_V2 = "pairwise_v2"
CHUNKER_FULL_SESSION = "full_session"

# 萃取階段的 schema 約束方式；預設走 prompt 指引加後驗證，見 feature 文件 §4
EXTRACTION_PROMPT_GUIDED = "prompt_guided"
EXTRACTION_STRUCTURED_OUTPUT = "structured_output"

# 事件分裂策略
DISAGGREGATION_SINGLE_PASS = "single_pass"
DISAGGREGATION_TWO_STAGE = "two_stage"


def _env_int(key: str, default: int) -> int:
    """讀取整數環境變數；空字串與非法值都退回預設，避免部署設錯直接讓 Lambda 掛掉。"""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


@dataclass(frozen=True)
class ExtractionConfig:
    """一次 batch 執行所使用的萃取設定。"""

    # canonical key 的 Slot 粒度；寫入 event 後不可變更，改動只影響新事件
    event_slot_minutes: int = 30

    # 分類體系版本戳記；留空時由 concept_type_map.json 的 taxonomy_version 決定
    taxonomy_version: str | None = None

    chunker_type: str = CHUNKER_LLM_PROMPT
    extraction_mode: str = EXTRACTION_PROMPT_GUIDED
    disaggregation_mode: str = DISAGGREGATION_SINGLE_PASS

    # 候選細分類節點數；hackathon 消融實驗以 14 為最佳
    rac_top_k: int = 14

    # 對話模型；空字串代表沿用 shared.bedrock 的預設（目前是 Claude Opus 4.6 global profile）
    model_id: str = ""

    # 分階段覆寫：分類與分塊的 schema 固定、輸出短，可以換較便宜的模型；
    # 萃取是品質瓶頸，預設沿用主模型。空字串代表 fallback 到 `model_id`。
    classifier_model_id: str = ""
    extractor_model_id: str = ""
    chunker_model_id: str = ""

    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024
    # index 維度在建立時固定，因此名稱帶模型與維度；換模型是新建索引並存而非改舊的
    concept_vector_index: str = "uco-concepts-titan-v2-1024"
    concept_vector_bucket: str = ""

    # 寫入 session／turn 的版本戳記，供重跑與稽核比對
    chunk_planner_version: str = "chunk-planner-1"
    batch_extractor_version: str = "batch-extractor-1"

    taxonomy_assets_dir: Path = field(default=TAXONOMY_ASSETS_DIR)
    retrieval_assets_dir: Path = field(default=RETRIEVAL_ASSETS_DIR)
    segmenter_assets_dir: Path = field(default=SEGMENTER_ASSETS_DIR)

    def model_for(self, stage: str) -> str | None:
        """取某個階段要用的模型；回 None 代表交給 `shared.bedrock` 的預設。

        階段：`classifier`、`extractor`、`chunker`。未特別指定時沿用 `model_id`。
        """
        specific = {
            "classifier": self.classifier_model_id,
            "extractor": self.extractor_model_id,
            "chunker": self.chunker_model_id,
        }.get(stage, "")
        return specific or self.model_id or None

    @classmethod
    def from_env(cls) -> "ExtractionConfig":
        """由環境變數建立設定。"""
        return cls(
            event_slot_minutes=_env_int("EVENT_SLOT_MINUTES", 30),
            taxonomy_version=os.environ.get("TAXONOMY_VERSION", "").strip() or None,
            chunker_type=_env_str("CHUNKER_TYPE", CHUNKER_LLM_PROMPT),
            extraction_mode=_env_str("EXTRACTION_MODE", EXTRACTION_PROMPT_GUIDED),
            disaggregation_mode=_env_str("DISAGGREGATION_MODE", DISAGGREGATION_SINGLE_PASS),
            rac_top_k=_env_int("RAC_TOP_K", 14),
            model_id=_env_str("BEDROCK_MODEL_ID", ""),
            classifier_model_id=_env_str("BEDROCK_CLASSIFIER_MODEL_ID", ""),
            extractor_model_id=_env_str("BEDROCK_EXTRACTOR_MODEL_ID", ""),
            chunker_model_id=_env_str("BEDROCK_CHUNKER_MODEL_ID", ""),
            embedding_model_id=_env_str("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
            embedding_dim=_env_int("EMBEDDING_DIM", 1024),
            concept_vector_index=_env_str("CONCEPT_VECTOR_INDEX", "uco-concepts-titan-v2-1024"),
            concept_vector_bucket=_env_str("CONCEPT_VECTOR_BUCKET", ""),
            chunk_planner_version=_env_str("CHUNK_PLANNER_VERSION", "chunk-planner-1"),
            batch_extractor_version=_env_str("BATCH_EXTRACTOR_VERSION", "batch-extractor-1"),
        )
