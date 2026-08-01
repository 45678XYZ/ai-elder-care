"""容器啟動設定與聲紋解析。"""

import pytest

from app.config import ConfigError, ContainerConfig, load_config, resolve_speaker_dir

BASE_ENV = {
    "TTS_MODEL_ID": "MediaTek-Research/BreezyVoice",
    "TTS_MODEL_REVISION": "main",
    "TTS_LANGUAGES": "zh-TW",
    "TTS_DIALECTS": "",
    "TTS_DEFAULT_SPEAKER": "",
}


def test_loads_terraform_injected_environment():
    config = load_config({**BASE_ENV, "TTS_MODEL_ROOT": "/opt/ml/model"})

    assert config.model_id == "MediaTek-Research/BreezyVoice"
    assert config.languages == frozenset({"zh-TW"})
    assert config.dialects == frozenset()
    assert config.weights_dir.as_posix() == "/opt/ml/model/breezyvoice"
    assert config.speakers_dir.as_posix() == "/opt/ml/model/speakers"


def test_parses_multi_valued_language_and_dialect_lists():
    config = load_config({**BASE_ENV, "TTS_LANGUAGES": "zh-TW, hak", "TTS_DIALECTS": "a, b"})

    assert config.languages == frozenset({"zh-TW", "hak"})
    assert config.dialects == frozenset({"a", "b"})


def test_revision_falls_back_to_main_when_blank():
    config = load_config({**BASE_ENV, "TTS_MODEL_REVISION": "  "})

    assert config.model_revision == "main"


@pytest.mark.parametrize(
    "override",
    [
        {"TTS_MODEL_ID": ""},
        {"TTS_LANGUAGES": ""},
        {"TTS_LANGUAGES": " , "},
        {"TTS_MAX_TEXT_CHARS": "0"},
        {"TTS_MAX_TEXT_CHARS": "-1"},
        {"TTS_MAX_TEXT_CHARS": "abc"},
    ],
)
def test_fails_fast_on_incomplete_configuration(override):
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, **override})


def _config_with_speakers(tmp_path, *names, default="") -> ContainerConfig:
    for name in names:
        speaker_dir = tmp_path / "speakers" / name
        speaker_dir.mkdir(parents=True)
        (speaker_dir / "prompt.wav").write_bytes(b"RIFF")
    return ContainerConfig(
        model_id="MediaTek-Research/BreezyVoice",
        model_revision="main",
        languages=frozenset({"zh-TW"}),
        dialects=frozenset(),
        default_speaker=default,
        model_root=tmp_path,
        max_text_chars=3000,
    )


def test_resolves_default_speaker_when_request_omits_one(tmp_path):
    config = _config_with_speakers(tmp_path, "default")

    resolved = resolve_speaker_dir(config, None)

    assert resolved == tmp_path / "speakers" / "default"


def test_configured_default_speaker_wins_over_literal_default(tmp_path):
    config = _config_with_speakers(tmp_path, "default", "grandma", default="grandma")

    assert resolve_speaker_dir(config, None) == tmp_path / "speakers" / "grandma"


def test_rejects_unknown_speaker(tmp_path):
    config = _config_with_speakers(tmp_path, "default")

    with pytest.raises(ValueError):
        resolve_speaker_dir(config, "nobody")


@pytest.mark.parametrize("speaker", ["../secrets", "a/b", ".", ".hidden", " "])
def test_rejects_path_traversal_in_speaker(tmp_path, speaker):
    """speaker 直接參與路徑組合，必須擋掉逃逸字元，不能讓 request 指到任意檔案。"""
    config = _config_with_speakers(tmp_path, "default")

    with pytest.raises(ValueError):
        resolve_speaker_dir(config, speaker)
