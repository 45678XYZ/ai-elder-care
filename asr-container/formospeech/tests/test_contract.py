"""驗證 request 契約；規格見 docs/asr/sagemaker-inference-contract.md。"""

import pytest

from app.config import MAX_AUDIO_BYTES, ContainerConfig
from app.contract import ContractError, parse_custom_attributes, validate_request

VALID_ATTRIBUTES = "language=hak;sample_rate_hz=16000;channels=1"


@pytest.fixture
def config(tmp_path) -> ContainerConfig:
    return ContainerConfig(
        model_id="formospeech/whisper-large-v3-taiwanese-hakka",
        model_revision="main",
        languages=frozenset({"hak"}),
        prompt_id="htia_sixian",
        generation_language="Chinese",
        model_root=tmp_path,
    )


def _pcm(sample_count: int) -> bytes:
    return b"\x00\x01" * sample_count


def test_accepts_canonical_request(config):
    language = validate_request(_pcm(16000), VALID_ATTRIBUTES, config)

    assert language == "hak"


def test_parses_custom_attributes_with_surrounding_whitespace():
    parsed = parse_custom_attributes(" language=hak ; sample_rate_hz=16000 ; channels=1 ")

    assert parsed == {"language": "hak", "sample_rate_hz": "16000", "channels": "1"}


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_rejects_missing_custom_attributes(raw):
    with pytest.raises(ContractError) as excinfo:
        parse_custom_attributes(raw)

    assert excinfo.value.code in {"missing_custom_attributes", "malformed_custom_attributes"}


@pytest.mark.parametrize("raw", ["language", "=hak", "language=hak;bad", ";;"])
def test_rejects_malformed_custom_attributes(raw):
    with pytest.raises(ContractError):
        parse_custom_attributes(raw)


@pytest.mark.parametrize(
    ("attributes", "code"),
    [
        ("language=zh-TW;sample_rate_hz=16000;channels=1", "unsupported_language"),
        ("language=;sample_rate_hz=16000;channels=1", "unsupported_language"),
        ("sample_rate_hz=16000;channels=1", "unsupported_language"),
        ("language=hak;sample_rate_hz=8000;channels=1", "unsupported_sample_rate"),
        ("language=hak;channels=1", "unsupported_sample_rate"),
        ("language=hak;sample_rate_hz=16000;channels=2", "unsupported_channel_count"),
        ("language=hak;sample_rate_hz=16000", "unsupported_channel_count"),
    ],
)
def test_rejects_non_canonical_audio_attributes(config, attributes, code):
    """Formo 只服務 hak；收到 zh-TW 代表 router 走錯，必須擋掉而非默默辨識。"""
    with pytest.raises(ContractError) as excinfo:
        validate_request(_pcm(16000), attributes, config)

    assert excinfo.value.code == code


def test_rejects_empty_audio(config):
    with pytest.raises(ContractError) as excinfo:
        validate_request(b"", VALID_ATTRIBUTES, config)

    assert excinfo.value.code == "empty_audio"


def test_accepts_audio_at_the_sixty_second_limit(config):
    assert validate_request(b"\x00" * MAX_AUDIO_BYTES, VALID_ATTRIBUTES, config) == "hak"


def test_rejects_audio_over_the_sixty_second_limit(config):
    with pytest.raises(ContractError) as excinfo:
        validate_request(b"\x00" * (MAX_AUDIO_BYTES + 2), VALID_ATTRIBUTES, config)

    assert excinfo.value.code == "audio_too_long"


def test_rejects_odd_length_body_because_s16le_needs_pairs(config):
    """長度為奇數代表 body 被截斷或根本不是 PCM S16LE。"""
    with pytest.raises(ContractError) as excinfo:
        validate_request(b"\x00\x01\x02", VALID_ATTRIBUTES, config)

    assert excinfo.value.code == "malformed_audio"
