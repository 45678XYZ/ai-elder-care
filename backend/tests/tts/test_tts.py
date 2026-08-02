"""TTS 設定、能力路由與 provider contract 測試；不連真實網路。"""

import io
import json
import time

import pytest

from src.shared.tts.composition import build_facade, build_provider_registry
from src.shared.tts.config import ConfigParseError, parse_tts_config
from src.shared.tts.providers import PollyTtsProvider, SageMakerTtsProvider
from src.shared.tts.types import (
    CancellationSignal,
    CorrelationContext,
    Deadline,
    HakkaDialect,
    Language,
    SynthesizedAudio,
    TtsErrorCategory,
    TypedTtsError,
)


def _gate(value=True):
    return {
        "staging_validation_passed": value,
        "license_cleared": value,
        "access_granted": value,
        "quota_cleared": value,
        "runtime_capacity_verified": value,
        "latency_slo_verified": value,
    }


def _config():
    return parse_tts_config(_config_dict())


def _config_dict():
    return (
        {
            "schema_version": 1,
            "routes": {
                "hak:htia_sixian": {
                    "route": "hak_sixian",
                    "enabled": True,
                    "provider_identifier": "omni",
                    "fallback_chain": ["vox"],
                },
                "hak:htia_nansixian": {
                    "route": "hak_nansixian",
                    "enabled": True,
                    "provider_identifier": "omni",
                    "fallback_chain": ["vox"],
                },
            },
            "providers": {
                "omni": {
                    "kind": "remote_model",
                    "status": "enabled",
                    "languages": ["hak"],
                    "dialects": ["htia_sixian", "htia_nansixian"],
                    "metadata_ref": "omni",
                    "endpoint_name": "omni-endpoint",
                },
                "vox": {
                    "kind": "remote_model",
                    "status": "enabled",
                    "languages": ["hak"],
                    "dialects": ["htia_sixian"],
                    "metadata_ref": "vox",
                    "endpoint_name": "vox-endpoint",
                    "speaker": "XF",
                },
            },
            "model_metadata": {
                "omni": {
                    "model_id": "formospeech/omnivoice-hakka-community-1",
                    "revision": "main",
                    "license": "CC BY-NC 4.0",
                    "approved_for_production": True,
                    "production_gate": _gate(),
                },
                "vox": {
                    "model_id": "formospeech/yourtts-htia-240704",
                    "revision": "main",
                    "license": "CC BY-NC 4.0",
                    "approved_for_production": True,
                    "production_gate": _gate(),
                },
            },
        }
    )


class _Provider:
    def __init__(self, provider_id, result, calls):
        self.provider_id = provider_id
        self._result = result
        self._calls = calls

    def synthesize(self, text, language, dialect, deadline, cancellation):
        self._calls.append((self.provider_id, language, dialect))
        return self._result


def _synthesize(facade, dialect):
    return facade.synthesize(
        text="食飽吂？",
        language=Language.HAK,
        dialect=dialect,
        deadline=Deadline.after(2, time.monotonic),
        cancellation=CancellationSignal(),
        context=CorrelationContext("corr-1"),
    )


def test_sixian_fails_over_to_voxhakka_in_same_language():
    calls = []
    failure = TypedTtsError(
        TtsErrorCategory.PROVIDER_UNAVAILABLE, "down", True
    )
    facade = build_facade(
        _config(),
        {
            "omni": _Provider("omni", failure, calls),
            "vox": _Provider("vox", SynthesizedAudio(b"mp3", "vox"), calls),
        },
    )

    result = _synthesize(facade, HakkaDialect.SIXIAN)

    assert isinstance(result, SynthesizedAudio)
    assert result.provider_id == "vox"
    assert [entry[0] for entry in calls] == ["omni", "vox"]
    assert all(entry[1] is Language.HAK for entry in calls)


def test_nansixian_never_calls_voxhakka():
    calls = []
    failure = TypedTtsError(
        TtsErrorCategory.PROVIDER_UNAVAILABLE, "down", True
    )
    facade = build_facade(
        _config(),
        {
            "omni": _Provider("omni", failure, calls),
            "vox": _Provider("vox", SynthesizedAudio(b"mp3", "vox"), calls),
        },
    )

    result = _synthesize(facade, HakkaDialect.NANSIXIAN)

    assert isinstance(result, TypedTtsError)
    assert [entry[0] for entry in calls] == ["omni"]


def test_incomplete_gate_does_not_build_remote_provider():
    raw = _config()
    metadata = raw.model_metadata["omni"]
    assert metadata.is_production_allowed
    config_dict = {
        "schema_version": 1,
        "routes": {},
        "providers": {
            "omni": {
                "kind": "remote_model",
                "status": "enabled",
                "languages": ["hak"],
                "dialects": ["htia_sixian"],
                "metadata_ref": "omni",
                "endpoint_name": "endpoint",
            }
        },
        "model_metadata": {
            "omni": {
                "model_id": "formospeech/omnivoice-hakka-community-1",
                "revision": "main",
                "license": "CC BY-NC 4.0",
                "approved_for_production": True,
                "production_gate": _gate(False),
            }
        },
    }
    assert build_provider_registry(parse_tts_config(config_dict)) == {}


def test_invalid_route_reference_fails_closed():
    with pytest.raises(ConfigParseError):
        parse_tts_config(
            {
                "schema_version": 1,
                "routes": {
                    "zh-TW": {
                        "route": "bad",
                        "enabled": True,
                        "provider_identifier": "missing",
                    }
                },
                "providers": {},
                "model_metadata": {},
            }
        )


def test_sagemaker_contract_contains_explicit_language_and_dialect():
    class Client:
        def invoke_endpoint(self, **kwargs):
            self.kwargs = kwargs
            return {"Body": io.BytesIO(b"mp3"), "ContentType": "audio/mpeg"}

    client = Client()
    provider = SageMakerTtsProvider(_config().providers["vox"], client)
    result = provider.synthesize(
        "食飽吂？",
        Language.HAK,
        HakkaDialect.SIXIAN,
        Deadline.after(2, time.monotonic),
        CancellationSignal(),
    )

    assert isinstance(result, SynthesizedAudio)
    payload = json.loads(client.kwargs["Body"])
    assert payload == {
        "text": "食飽吂？",
        "language": "hak",
        "format": "mp3",
        "dialect": "htia_sixian",
        "speaker": "XF",
    }


def test_polly_uses_configured_engine_and_voice():
    class Client:
        def synthesize_speech(self, **kwargs):
            self.kwargs = kwargs
            return {"AudioStream": io.BytesIO(b"mp3")}

    config = parse_tts_config(
        {
            "schema_version": 1,
            "routes": {},
            "providers": {
                "polly": {
                    "kind": "aws_managed",
                    "status": "enabled",
                    "languages": ["zh-TW"],
                    "voice_id": "Zhiyu",
                    "engine": "neural",
                }
            },
            "model_metadata": {},
        }
    )
    client = Client()
    result = PollyTtsProvider(config.providers["polly"], client).synthesize(
        "請記得喝水。",
        Language.ZH_TW,
        None,
        Deadline.after(2, time.monotonic),
        CancellationSignal(),
    )

    assert isinstance(result, SynthesizedAudio)
    assert client.kwargs["Engine"] == "neural"
    assert client.kwargs["VoiceId"] == "Zhiyu"


# ─────────────────────────────────────────────────────────────────
# 非同步 TTS：合成前的可用性判定
# ─────────────────────────────────────────────────────────────────
def _facade_with(config, calls=None):
    registry = {
        "omni": _Provider("omni", None, calls if calls is not None else []),
        "vox": _Provider("vox", None, calls if calls is not None else []),
    }
    return build_facade(config, registry)


def test_is_available_is_true_for_an_approved_route():
    """chat 靠這個判斷要不要讓 App 等音訊，不能為了問而先合成一次。"""
    calls = []
    facade = _facade_with(_config(), calls)

    assert facade.is_available(Language.HAK, HakkaDialect.SIXIAN) is True
    # 判定不得觸發任何 provider 呼叫
    assert calls == []


def test_is_available_is_false_when_the_gate_is_not_approved():
    raw = _config_dict()
    raw["model_metadata"]["omni"]["approved_for_production"] = False
    raw["model_metadata"]["omni"]["production_gate"] = _gate(False)
    raw["model_metadata"]["vox"]["approved_for_production"] = False
    raw["model_metadata"]["vox"]["production_gate"] = _gate(False)
    facade = _facade_with(parse_tts_config(raw))

    assert facade.is_available(Language.HAK, HakkaDialect.SIXIAN) is False


def test_is_available_is_false_for_an_unrouted_dialect():
    facade = _facade_with(_config())

    assert facade.is_available(Language.HAK, HakkaDialect.DAPU) is False


def test_is_available_requires_a_dialect_for_hakka():
    """客語沒帶腔調時 synthesize 會被擋下，可用性判定必須給同樣的答案。"""
    facade = _facade_with(_config())

    assert facade.is_available(Language.HAK, None) is False
