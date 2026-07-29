"""ExtractionConfig 測試：env 驅動與分階段模型覆寫。"""

import pytest

from src.extraction.config import (
    CHUNKER_EMBEDDING_DEPTH,
    CHUNKER_LLM_PROMPT,
    DISAGGREGATION_SINGLE_PASS,
    EXTRACTION_PROMPT_GUIDED,
    ExtractionConfig,
)
from src.shared import bedrock


def test_defaults(monkeypatch):
    for key in (
        "EVENT_SLOT_MINUTES",
        "CHUNKER_TYPE",
        "EXTRACTION_MODE",
        "RAC_TOP_K",
        "BEDROCK_MODEL_ID",
        "TAXONOMY_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)

    config = ExtractionConfig.from_env()
    assert config.event_slot_minutes == 30
    assert config.chunker_type == CHUNKER_LLM_PROMPT
    assert config.extraction_mode == EXTRACTION_PROMPT_GUIDED
    assert config.disaggregation_mode == DISAGGREGATION_SINGLE_PASS
    assert config.rac_top_k == 14
    assert config.taxonomy_version is None
    # 未設定時交給 shared.bedrock 的預設，不在此處寫死模型 ID
    assert config.model_for("extractor") is None


def test_invalid_int_falls_back_to_default(monkeypatch):
    """部署把數值設錯不該讓 Lambda 直接掛掉。"""
    monkeypatch.setenv("EVENT_SLOT_MINUTES", "abc")
    monkeypatch.setenv("RAC_TOP_K", "")
    config = ExtractionConfig.from_env()
    assert config.event_slot_minutes == 30
    assert config.rac_top_k == 14


def test_single_model_applies_to_every_stage(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1:0")
    config = ExtractionConfig.from_env()
    for stage in ("classifier", "extractor", "chunker"):
        assert config.model_for(stage) == "global.anthropic.claude-opus-4-6-v1:0"


def test_per_stage_override(monkeypatch):
    """分類與分塊的輸出短、schema 固定，可以換便宜模型；萃取沿用主模型。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1:0")
    monkeypatch.setenv("BEDROCK_CLASSIFIER_MODEL_ID", "global.anthropic.claude-haiku-4-5-v1:0")
    config = ExtractionConfig.from_env()

    assert config.model_for("classifier") == "global.anthropic.claude-haiku-4-5-v1:0"
    assert config.model_for("extractor") == "global.anthropic.claude-opus-4-6-v1:0"
    assert config.model_for("chunker") == "global.anthropic.claude-opus-4-6-v1:0"


def test_unknown_stage_uses_main_model(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m1")
    assert ExtractionConfig.from_env().model_for("summary") == "m1"


def test_bedrock_default_model_is_flagship():
    """預設走 Anthropic 在 Bedrock 的旗艦模型與 global inference profile。"""
    assert bedrock.DEFAULT_MODEL_ID.startswith("global.")
    assert "opus" in bedrock.DEFAULT_MODEL_ID


def test_embedding_settings_are_env_driven(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "cohere.embed-multilingual-v3")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    monkeypatch.setenv("CONCEPT_VECTOR_INDEX", "uco-concepts-cohere-v3-1024")
    monkeypatch.setenv("CONCEPT_VECTOR_BUCKET", "my-vectors")
    config = ExtractionConfig.from_env()
    assert config.embedding_model_id == "cohere.embed-multilingual-v3"
    assert config.concept_vector_index == "uco-concepts-cohere-v3-1024"
    assert config.concept_vector_bucket == "my-vectors"


def test_chunker_type_is_env_driven(monkeypatch):
    monkeypatch.setenv("CHUNKER_TYPE", CHUNKER_EMBEDDING_DEPTH)
    assert ExtractionConfig.from_env().chunker_type == CHUNKER_EMBEDDING_DEPTH
