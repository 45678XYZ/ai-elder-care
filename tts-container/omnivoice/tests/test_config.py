"""容器啟動設定、腔調對照與聲紋解析。"""

import pytest

from app.config import (
    DIALECT_INSTRUCTIONS,
    ConfigError,
    ContainerConfig,
    load_config,
    resolve_speaker_dir,
)

ALL_DIALECTS = (
    "htia_sixian,htia_hailu,htia_dapu,htia_raoping,htia_zhaoan,htia_nansixian"
)
BASE_ENV = {
    "TTS_MODEL_ID": "formospeech/omnivoice-hakka-community-1",
    "TTS_MODEL_REVISION": "main",
    "TTS_LANGUAGES": "hak",
    "TTS_DIALECTS": ALL_DIALECTS,
    "TTS_DEFAULT_SPEAKER": "",
}


def test_loads_terraform_injected_environment():
    config = load_config({**BASE_ENV, "TTS_MODEL_ROOT": "/opt/ml/model"})

    assert config.languages == frozenset({"hak"})
    assert len(config.dialects) == 6
    assert config.weights_dir.as_posix() == "/opt/ml/model/omnivoice"
    assert config.speakers_dir.as_posix() == "/opt/ml/model/speakers"


def test_every_supported_dialect_has_an_instruct_string():
    """對照表少一腔會讓該腔在執行期才失敗，這裡先擋住。"""
    assert set(DIALECT_INSTRUCTIONS) == set(ALL_DIALECTS.split(","))
    assert all(value.startswith("客語") for value in DIALECT_INSTRUCTIONS.values())


@pytest.mark.parametrize(
    "override",
    [
        {"TTS_MODEL_ID": ""},
        {"TTS_LANGUAGES": ""},
        {"TTS_DIALECTS": ""},
        {"TTS_DIALECTS": "htia_sixian,htia_unknown"},
        {"TTS_MAX_TEXT_CHARS": "0"},
        {"TTS_MAX_TEXT_CHARS": "abc"},
    ],
)
def test_fails_fast_on_incomplete_configuration(override):
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, **override})


def test_unknown_dialect_error_does_not_echo_the_value():
    """設定錯誤訊息會進 log，不該把任意字串帶進去。"""
    with pytest.raises(ConfigError) as excinfo:
        load_config({**BASE_ENV, "TTS_DIALECTS": "htia_sixian,htia_leaky_value"})

    assert "htia_leaky_value" not in str(excinfo.value)


def _config_with_speakers(tmp_path, *names, default="") -> ContainerConfig:
    for name in names:
        speaker_dir = tmp_path / "speakers" / name
        speaker_dir.mkdir(parents=True)
        (speaker_dir / "prompt.wav").write_bytes(b"RIFF")
    return ContainerConfig(
        model_id="formospeech/omnivoice-hakka-community-1",
        model_revision="main",
        languages=frozenset({"hak"}),
        dialects=frozenset(DIALECT_INSTRUCTIONS),
        default_speaker=default,
        model_root=tmp_path,
        max_text_chars=3000,
    )


def test_resolves_default_speaker_when_request_omits_one(tmp_path):
    config = _config_with_speakers(tmp_path, "default")

    assert resolve_speaker_dir(config, None) == tmp_path / "speakers" / "default"


def test_rejects_unknown_speaker(tmp_path):
    config = _config_with_speakers(tmp_path, "default")

    with pytest.raises(ValueError):
        resolve_speaker_dir(config, "nobody")


@pytest.mark.parametrize("speaker", ["../secrets", "a/b", ".", ".hidden", " "])
def test_rejects_path_traversal_in_speaker(tmp_path, speaker):
    """speaker 直接參與路徑組合，必須擋掉逃逸字元。"""
    config = _config_with_speakers(tmp_path, "default")

    with pytest.raises(ValueError):
        resolve_speaker_dir(config, speaker)
