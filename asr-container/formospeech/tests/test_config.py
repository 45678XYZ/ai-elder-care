"""容器啟動設定；prompt 與 generation language 都是部署期固定值。"""

import pytest

from app.config import SUPPORTED_PROMPT_IDS, ConfigError, load_config

BASE_ENV = {
    "ASR_MODEL_ID": "formospeech/whisper-large-v3-taiwanese-hakka",
    "ASR_MODEL_REVISION": "main",
    "ASR_LANGUAGES": "hak",
    "FORMO_GENERATION_LANGUAGE": "Chinese",
    "FORMO_PROMPT_ID": "htia_sixian",
}


def test_loads_terraform_injected_environment():
    config = load_config({**BASE_ENV, "ASR_MODEL_ROOT": "/opt/ml/model"})

    assert config.languages == frozenset({"hak"})
    assert config.prompt_id == "htia_sixian"
    assert config.generation_language == "Chinese"
    assert config.weights_dir.as_posix() == "/opt/ml/model/formospeech"


@pytest.mark.parametrize("prompt_id", sorted(SUPPORTED_PROMPT_IDS))
def test_accepts_every_hakka_wire_value(prompt_id):
    """六個 endpoint 共用映像，每一腔都必須能啟動。"""
    assert load_config({**BASE_ENV, "FORMO_PROMPT_ID": prompt_id}).prompt_id == prompt_id


def test_revision_falls_back_to_main_when_blank():
    assert load_config({**BASE_ENV, "ASR_MODEL_REVISION": " "}).model_revision == "main"


@pytest.mark.parametrize(
    "override",
    [
        {"ASR_MODEL_ID": ""},
        {"ASR_LANGUAGES": ""},
        {"FORMO_PROMPT_ID": ""},
        {"FORMO_PROMPT_ID": "htia_unknown"},
        {"FORMO_PROMPT_ID": "sixian"},
        {"FORMO_GENERATION_LANGUAGE": ""},
    ],
)
def test_fails_fast_on_incomplete_configuration(override):
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, **override})


def test_prompt_error_does_not_echo_the_value():
    """設定錯誤訊息會進啟動 log，不該把任意字串帶進去。"""
    with pytest.raises(ConfigError) as excinfo:
        load_config({**BASE_ENV, "FORMO_PROMPT_ID": "htia_leaky_value"})

    assert "htia_leaky_value" not in str(excinfo.value)
