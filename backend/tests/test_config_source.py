"""設定來源解析的行為：環境變數優先、SSM 為備援、取得失敗一律 fail closed。"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from src.shared import config_source
from src.shared.config_source import ConfigSourceError, load_raw_config

JSON_ENV = "ASR_CONFIG_JSON"
PARAM_ENV = "ASR_CONFIG_SSM_PARAMETER"


class FakeSsmClient:
    """以「回傳值」或「要拋的例外」驅動的假 SSM client。"""

    def __init__(self, value: str | None = None, error: Exception | None = None):
        self.value = value
        self.error = error
        self.calls: list[dict[str, object]] = []

    def get_parameter(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Parameter": {"Value": self.value}}


@pytest.fixture
def fake_ssm(monkeypatch):
    """安裝假 client，並確保測試不會意外建立真的 boto3 client。"""

    def _install(client: FakeSsmClient) -> FakeSsmClient:
        monkeypatch.setattr(config_source, "_client", lambda: client)
        return client

    return _install


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv(JSON_ENV, raising=False)
    monkeypatch.delenv(PARAM_ENV, raising=False)


def test_env_json_is_returned_directly(monkeypatch, fake_ssm):
    client = fake_ssm(FakeSsmClient(value="from-ssm"))
    monkeypatch.setenv(JSON_ENV, '{"schema_version": 1}')

    assert load_raw_config(JSON_ENV, PARAM_ENV) == '{"schema_version": 1}'
    # 環境變數夠用時不該付出 SSM 呼叫的 cold start 成本。
    assert client.calls == []


def test_env_json_wins_over_parameter(monkeypatch, fake_ssm):
    client = fake_ssm(FakeSsmClient(value="from-ssm"))
    monkeypatch.setenv(JSON_ENV, "from-env")
    monkeypatch.setenv(PARAM_ENV, "/e-hakka-care/asr/config")

    assert load_raw_config(JSON_ENV, PARAM_ENV) == "from-env"
    assert client.calls == []


def test_returns_none_when_neither_source_is_set(fake_ssm):
    fake_ssm(FakeSsmClient(value="from-ssm"))

    assert load_raw_config(JSON_ENV, PARAM_ENV) is None


def test_blank_env_json_falls_through_to_parameter(monkeypatch, fake_ssm):
    fake_ssm(FakeSsmClient(value="from-ssm"))
    monkeypatch.setenv(JSON_ENV, "   ")
    monkeypatch.setenv(PARAM_ENV, "/e-hakka-care/asr/config")

    assert load_raw_config(JSON_ENV, PARAM_ENV) == "from-ssm"


def test_parameter_is_read_with_decryption(monkeypatch, fake_ssm):
    client = fake_ssm(FakeSsmClient(value="from-ssm"))
    monkeypatch.setenv(PARAM_ENV, "  /e-hakka-care/asr/config  ")

    assert load_raw_config(JSON_ENV, PARAM_ENV) == "from-ssm"
    assert client.calls == [
        {"Name": "/e-hakka-care/asr/config", "WithDecryption": True}
    ]


def test_ssm_failure_raises_instead_of_falling_back(monkeypatch, fake_ssm):
    """權限或網路問題必須是明確錯誤，不能安靜地退回預設設定。"""
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetParameter",
    )
    fake_ssm(FakeSsmClient(error=error))
    monkeypatch.setenv(PARAM_ENV, "/e-hakka-care/asr/config")

    with pytest.raises(ConfigSourceError):
        load_raw_config(JSON_ENV, PARAM_ENV)


def test_missing_parameter_raises(monkeypatch, fake_ssm):
    error = ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
        "GetParameter",
    )
    fake_ssm(FakeSsmClient(error=error))
    monkeypatch.setenv(PARAM_ENV, "/e-hakka-care/asr/config")

    with pytest.raises(ConfigSourceError):
        load_raw_config(JSON_ENV, PARAM_ENV)


def test_empty_parameter_value_raises(monkeypatch, fake_ssm):
    fake_ssm(FakeSsmClient(value="   "))
    monkeypatch.setenv(PARAM_ENV, "/e-hakka-care/asr/config")

    with pytest.raises(ConfigSourceError):
        load_raw_config(JSON_ENV, PARAM_ENV)
