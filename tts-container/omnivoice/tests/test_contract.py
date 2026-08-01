"""驗證 request 契約；規格見 docs/tts/sagemaker-inference-contract.md。"""

import json

import pytest

from app.config import ContainerConfig
from app.contract import ContractError, parse_payload


@pytest.fixture
def config(tmp_path) -> ContainerConfig:
    return ContainerConfig(
        model_id="formospeech/omnivoice-hakka-community-1",
        model_revision="main",
        languages=frozenset({"hak"}),
        dialects=frozenset({"htia_sixian", "htia_hailu", "htia_nansixian"}),
        default_speaker="",
        model_root=tmp_path,
        max_text_chars=20,
    )


def _body(**fields) -> bytes:
    return json.dumps(fields, ensure_ascii=False).encode("utf-8")


def test_parses_valid_hakka_payload(config):
    request = parse_payload(
        _body(text="食飽吂？", language="hak", dialect="htia_hailu", format="mp3"), config
    )

    assert request.text == "食飽吂？"
    assert request.dialect == "htia_hailu"
    assert request.speaker is None


def test_format_defaults_to_mp3_when_absent(config):
    request = parse_payload(_body(text="食飽吂？", language="hak", dialect="htia_sixian"), config)

    assert request.dialect == "htia_sixian"


def test_requires_dialect_because_hakka_route_has_six(config):
    """沒有腔調就沒有正確的 instruct，不能隨便挑一個代替。"""
    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text="食飽吂？", language="hak"), config)

    assert excinfo.value.code == "unsupported_dialect"


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"language": "hak", "dialect": "htia_sixian"}, "invalid_text"),
        ({"text": "  ", "language": "hak", "dialect": "htia_sixian"}, "invalid_text"),
        ({"text": "食飽吂", "language": "zh-TW", "dialect": "htia_sixian"}, "unsupported_language"),
        ({"text": "食飽吂", "dialect": "htia_sixian"}, "unsupported_language"),
        ({"text": "食飽吂", "language": "hak", "dialect": "htia_dapu"}, "unsupported_dialect"),
        ({"text": "食飽吂", "language": "hak", "dialect": 1}, "unsupported_dialect"),
        (
            {"text": "食飽吂", "language": "hak", "dialect": "htia_sixian", "format": "wav"},
            "unsupported_format",
        ),
        (
            {"text": "食飽吂", "language": "hak", "dialect": "htia_sixian", "speaker": 1},
            "invalid_speaker",
        ),
    ],
)
def test_rejects_invalid_fields(config, fields, code):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(json.dumps(fields).encode("utf-8"), config)

    assert excinfo.value.code == code


def test_rejects_dialect_outside_this_endpoint_configuration(config):
    """htia_dapu 是合法的六腔之一，但這個 endpoint 沒設定它，仍必須擋掉。"""
    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text="食飽吂", language="hak", dialect="htia_dapu"), config)

    assert excinfo.value.code == "unsupported_dialect"


def test_rejects_text_over_configured_limit(config):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text="字" * 21, language="hak", dialect="htia_sixian"), config)

    assert excinfo.value.code == "text_too_long"


@pytest.mark.parametrize("raw", [b"", b"not json", b'["list"]', b"\xff\xfe", b"null"])
def test_rejects_malformed_body(config, raw):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(raw, config)

    assert excinfo.value.code == "malformed_body"


def test_error_code_never_echoes_input(config):
    """錯誤代碼不得帶出原始文字，否則會經由 response body 外洩合成內容。"""
    secret = "長者的私密內容"

    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text=secret, language="ja-JP", dialect="htia_sixian"), config)

    assert secret not in str(excinfo.value)
