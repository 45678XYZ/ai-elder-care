"""驗證 request 契約；規格見 docs/tts/sagemaker-inference-contract.md。"""

import json

import pytest

from app.config import ContainerConfig
from app.contract import ContractError, parse_payload


@pytest.fixture
def config(tmp_path) -> ContainerConfig:
    return ContainerConfig(
        model_id="MediaTek-Research/BreezyVoice",
        model_revision="main",
        languages=frozenset({"zh-TW"}),
        dialects=frozenset(),
        default_speaker="",
        model_root=tmp_path,
        max_text_chars=20,
    )


def _body(**fields) -> bytes:
    return json.dumps(fields, ensure_ascii=False).encode("utf-8")


def test_parses_minimal_valid_payload(config):
    request = parse_payload(_body(text="今天天氣真好", language="zh-TW", format="mp3"), config)

    assert request.text == "今天天氣真好"
    assert request.speaker is None


def test_format_defaults_to_mp3_when_absent(config):
    request = parse_payload(_body(text="早安", language="zh-TW"), config)

    assert request.text == "早安"


def test_speaker_is_passed_through(config):
    request = parse_payload(_body(text="早安", language="zh-TW", speaker="default"), config)

    assert request.speaker == "default"


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"language": "zh-TW"}, "invalid_text"),
        ({"text": "", "language": "zh-TW"}, "invalid_text"),
        ({"text": "   ", "language": "zh-TW"}, "invalid_text"),
        ({"text": 123, "language": "zh-TW"}, "invalid_text"),
        ({"text": "早安"}, "unsupported_language"),
        ({"text": "早安", "language": "cmn-CN"}, "unsupported_language"),
        ({"text": "早安", "language": "hak"}, "unsupported_language"),
        ({"text": "早安", "language": "zh-TW", "format": "wav"}, "unsupported_format"),
        ({"text": "早安", "language": "zh-TW", "speaker": 1}, "invalid_speaker"),
    ],
)
def test_rejects_invalid_fields(config, fields, code):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(json.dumps(fields).encode("utf-8"), config)

    assert excinfo.value.code == code


def test_rejects_text_over_configured_limit(config):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text="字" * 21, language="zh-TW"), config)

    assert excinfo.value.code == "text_too_long"


def test_rejects_dialect_because_mandarin_route_has_none(config):
    """收到 dialect 代表 Lambda 把客語路由送錯了，必須擋掉而非忽略。"""
    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text="早安", language="zh-TW", dialect="htia_hailu"), config)

    assert excinfo.value.code == "unsupported_dialect"


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json", b'["list"]', b'"string"', b"\xff\xfe", b"null"],
)
def test_rejects_malformed_body(config, raw):
    with pytest.raises(ContractError) as excinfo:
        parse_payload(raw, config)

    assert excinfo.value.code == "malformed_body"


def test_error_code_never_echoes_input(config):
    """錯誤代碼不得帶出原始文字，否則會經由 response body 外洩合成內容。"""
    secret = "長者的私密內容"

    with pytest.raises(ContractError) as excinfo:
        parse_payload(_body(text=secret, language="ja-JP"), config)

    assert secret not in str(excinfo.value)
