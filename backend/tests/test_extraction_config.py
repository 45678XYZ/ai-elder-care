"""ExtractionConfig 測試：env 驅動與分階段模型覆寫。"""

from src.extraction.pipeline import (
    EXTRACTION_PROMPT_GUIDED,
    ExtractionConfig,
)
from src.shared import bedrock


def test_defaults(monkeypatch):
    for key in (
        "EVENT_SLOT_MINUTES",
        "EXTRACTION_MODE",
        "BEDROCK_MODEL_ID",
        "TAXONOMY_VERSION",
        "SEVEN_BATCH_CHAR_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)

    config = ExtractionConfig.from_env()
    assert config.event_slot_minutes == 30
    assert config.extraction_mode == EXTRACTION_PROMPT_GUIDED
    assert config.taxonomy_version is None
    assert config.seven_batch_char_limit == 12000
    assert config.model_for("extractor") is None


def test_invalid_int_falls_back_to_default(monkeypatch):
    """部署把數值設錯不該讓 Lambda 直接掛掉。"""
    monkeypatch.setenv("EVENT_SLOT_MINUTES", "abc")
    monkeypatch.setenv("SEVEN_BATCH_CHAR_LIMIT", "")
    config = ExtractionConfig.from_env()
    assert config.event_slot_minutes == 30
    assert config.seven_batch_char_limit == 12000


def test_single_model_applies_to_extractor(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1:0")
    config = ExtractionConfig.from_env()
    assert config.model_for("extractor") == "global.anthropic.claude-opus-4-6-v1:0"


def test_per_stage_override(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1:0")
    monkeypatch.setenv("BEDROCK_EXTRACTOR_MODEL_ID", "global.anthropic.claude-haiku-4-5-v1:0")
    config = ExtractionConfig.from_env()
    assert config.model_for("extractor") == "global.anthropic.claude-haiku-4-5-v1:0"


def test_unknown_stage_uses_main_model(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m1")
    assert ExtractionConfig.from_env().model_for("summary") == "m1"


def test_bedrock_default_model_is_flagship():
    assert bedrock.DEFAULT_MODEL_ID.startswith("global.")
    assert "opus" in bedrock.DEFAULT_MODEL_ID


def test_embedding_settings_are_env_driven(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "cohere.embed-multilingual-v3")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    config = ExtractionConfig.from_env()
    assert config.embedding_model_id == "cohere.embed-multilingual-v3"
    assert config.embedding_dim == 1024


def test_seven_batch_char_limit_is_env_driven(monkeypatch):
    monkeypatch.setenv("SEVEN_BATCH_CHAR_LIMIT", "8000")
    assert ExtractionConfig.from_env().seven_batch_char_limit == 8000
