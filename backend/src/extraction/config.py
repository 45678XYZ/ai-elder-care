"""萃取 pipeline 的執行期設定。

所有可調行為一律由環境變數驅動；設定物件為不可變 dataclass，
於 Lambda handler 進入點建立一次後往下傳。
"""

from dataclasses import dataclass, field
from pathlib import Path
import os

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TAXONOMY_ASSETS_DIR = ASSETS_DIR / "taxonomy"

# 萃取階段的 schema 約束方式
EXTRACTION_PROMPT_GUIDED = "prompt_guided"
EXTRACTION_STRUCTURED_OUTPUT = "structured_output"


def _env_int(key: str, default: int) -> int:
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

    event_slot_minutes: int = 30
    taxonomy_version: str | None = None
    extraction_mode: str = EXTRACTION_PROMPT_GUIDED

    # 對話模型；空字串代表沿用 shared.bedrock 的預設
    model_id: str = ""
    extractor_model_id: str = ""

    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024

    # direct_seven pipeline 的 turn 分批字元上限
    seven_batch_char_limit: int = 12000

    batch_extractor_version: str = "batch-extractor-1"

    taxonomy_assets_dir: Path = field(default=TAXONOMY_ASSETS_DIR)

    def model_for(self, stage: str) -> str | None:
        """取某個階段要用的模型；回 None 代表交給 shared.bedrock 的預設。"""
        specific = {"extractor": self.extractor_model_id}.get(stage, "")
        return specific or self.model_id or None

    @classmethod
    def from_env(cls) -> "ExtractionConfig":
        return cls(
            event_slot_minutes=_env_int("EVENT_SLOT_MINUTES", 30),
            taxonomy_version=os.environ.get("TAXONOMY_VERSION", "").strip() or None,
            extraction_mode=_env_str("EXTRACTION_MODE", EXTRACTION_PROMPT_GUIDED),
            model_id=_env_str("BEDROCK_MODEL_ID", ""),
            extractor_model_id=_env_str("BEDROCK_EXTRACTOR_MODEL_ID", ""),
            embedding_model_id=_env_str("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
            embedding_dim=_env_int("EMBEDDING_DIM", 1024),
            seven_batch_char_limit=_env_int("SEVEN_BATCH_CHAR_LIMIT", 12000),
            batch_extractor_version=_env_str("BATCH_EXTRACTOR_VERSION", "batch-extractor-1"),
        )
