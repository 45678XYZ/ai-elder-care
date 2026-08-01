"""Quick validation tests for Task 2.1 domain types and config."""
from __future__ import annotations

import pytest

from src.shared.asr import (
    AccessStatus,
    ApprovalState,
    AsrConfig,
    AsrErrorCategory,
    CE_MODEL_METADATA,
    CancellationSignal,
    CanonicalAudio,
    ConfigParseError,
    CorrelationContext,
    Deadline,
    FORMO_MODEL_METADATA,
    InputFormat,
    Language,
    ModelMetadata,
    ProviderConfig,
    ProviderStatus,
    RouteConfig,
    Transcript,
    TypedAsrError,
    UsageRestriction,
    make_route_not_approved_error,
    make_unsupported_language_error,
    parse_asr_config,
)


# ─────────────────────────────────────────────────────────────────
# InputFormat
# ─────────────────────────────────────────────────────────────────
class TestInputFormat:
    def test_valid_wav(self) -> None:
        assert InputFormat.from_str("wav") == InputFormat.WAV

    def test_valid_m4a(self) -> None:
        assert InputFormat.from_str("m4a") == InputFormat.M4A

    def test_unsupported_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported audio format"):
            InputFormat.from_str("mp3")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            InputFormat.from_str("")


# ─────────────────────────────────────────────────────────────────
# Language
# ─────────────────────────────────────────────────────────────────
class TestLanguage:
    def test_valid_zh_tw(self) -> None:
        assert Language.from_str("zh-TW") == Language.ZH_TW

    def test_valid_hak(self) -> None:
        assert Language.from_str("hak") == Language.HAK

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported language"):
            Language.from_str("en")

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValueError):
            Language.from_str("ZH-TW")


# ─────────────────────────────────────────────────────────────────
# CorrelationContext
# ─────────────────────────────────────────────────────────────────
class TestCorrelationContext:
    def test_valid(self) -> None:
        ctx = CorrelationContext(correlation_id="abc-123")
        assert ctx.correlation_id == "abc-123"

    def test_blank_raises(self) -> None:
        with pytest.raises(ValueError):
            CorrelationContext(correlation_id="   ")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            CorrelationContext(correlation_id="")

    def test_immutable(self) -> None:
        ctx = CorrelationContext(correlation_id="x")
        with pytest.raises(Exception):
            ctx.correlation_id = "y"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────
# Transcript
# ─────────────────────────────────────────────────────────────────
class TestTranscript:
    def test_valid_trimmed(self) -> None:
        t = Transcript(text="  hello  ")
        assert t.text == "hello"

    def test_blank_raises(self) -> None:
        with pytest.raises(ValueError):
            Transcript(text="   ")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            Transcript(text="")

    def test_immutable(self) -> None:
        t = Transcript(text="hi")
        with pytest.raises(Exception):
            t.text = "bye"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────
# TypedAsrError
# ─────────────────────────────────────────────────────────────────
class TestTypedAsrError:
    def test_valid(self) -> None:
        err = TypedAsrError(
            category=AsrErrorCategory.INVALID_AUDIO, message="bad", retryable=False
        )
        assert err.category == AsrErrorCategory.INVALID_AUDIO
        assert err.message == "bad"
        assert err.retryable is False

    def test_all_categories_exist(self) -> None:
        expected = {
            "invalid_audio",
            "unsupported_audio_format",
            "audio_duration_exceeded",
            "unsupported_language",
            "route_not_approved",
            "deadline_exceeded",
            "cancelled",
            "provider_unavailable",
            "provider_invalid_response",
            "provider_failure",
        }
        actual = {c.value for c in AsrErrorCategory}
        assert actual == expected


# ─────────────────────────────────────────────────────────────────
# CanonicalAudio
# ─────────────────────────────────────────────────────────────────
class TestCanonicalAudio:
    def test_valid(self) -> None:
        ca = CanonicalAudio(
            pcm_s16le=b"\x00\x01" * 100,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bits=16,
            duration_ms=500,
            input_format=InputFormat.WAV,
        )
        assert ca.sample_rate_hz == 16000

    def test_empty_bytes_raises(self) -> None:
        with pytest.raises(ValueError):
            CanonicalAudio(
                pcm_s16le=b"",
                sample_rate_hz=16000,
                channels=1,
                sample_width_bits=16,
                duration_ms=0,
                input_format=InputFormat.WAV,
            )

    def test_wrong_sample_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            CanonicalAudio(
                pcm_s16le=b"\x00" * 10,
                sample_rate_hz=44100,
                channels=1,
                sample_width_bits=16,
                duration_ms=100,
                input_format=InputFormat.WAV,
            )


# ─────────────────────────────────────────────────────────────────
# Deadline
# ─────────────────────────────────────────────────────────────────
class TestDeadline:
    def test_not_expired(self) -> None:
        d = Deadline.create(expiry=15.0, clock=lambda: 10.0)
        assert not d.is_expired()

    def test_expired_at_boundary(self) -> None:
        d = Deadline.create(expiry=15.0, clock=lambda: 15.0)
        assert d.is_expired()

    def test_expired_past(self) -> None:
        d = Deadline.create(expiry=15.0, clock=lambda: 20.0)
        assert d.is_expired()


# ─────────────────────────────────────────────────────────────────
# CancellationSignal
# ─────────────────────────────────────────────────────────────────
class TestCancellationSignal:
    def test_not_triggered_initially(self) -> None:
        cs = CancellationSignal()
        assert not cs.is_triggered

    def test_trigger_irrecoverable(self) -> None:
        cs = CancellationSignal()
        cs.trigger()
        assert cs.is_triggered
        # Irrecoverable: still triggered
        assert cs.is_triggered


# ─────────────────────────────────────────────────────────────────
# Model Metadata Constants
# ─────────────────────────────────────────────────────────────────
class TestModelMetadataConstants:
    def test_ce_metadata(self) -> None:
        assert CE_MODEL_METADATA.model_id == "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0"
        assert CE_MODEL_METADATA.license == "other"
        assert CE_MODEL_METADATA.usage_restriction == UsageRestriction.COLAB_VALIDATION_ONLY
        assert not CE_MODEL_METADATA.is_production_allowed

    def test_formo_metadata(self) -> None:
        assert FORMO_MODEL_METADATA.model_id == "formospeech/whisper-large-v3-taiwanese-hakka"
        assert FORMO_MODEL_METADATA.license == "CC BY-NC 4.0"
        assert FORMO_MODEL_METADATA.access_status == AccessStatus.GATED
        assert FORMO_MODEL_METADATA.usage_restriction == UsageRestriction.COLAB_VALIDATION_ONLY
        assert not FORMO_MODEL_METADATA.is_production_allowed


# ─────────────────────────────────────────────────────────────────
# Config Parser
# ─────────────────────────────────────────────────────────────────
class TestConfigParser:
    def _valid_config_data(self) -> dict:
        return {
            "routes": {
                "zh-TW": {
                    "route": "aws_zh_tw",
                    "provider_identifier": "aws_zh_tw_adapter",
                    "enabled": True,
                },
                "hak": {
                    "route": "hak_mock",
                    "provider_identifier": "hak_mock",
                    "enabled": True,
                },
            },
            "providers": {
                "aws_zh_tw_adapter": {
                    "identifier": "aws_zh_tw_adapter",
                    "status": "enabled",
                    "metadata_ref": None,
                },
                "hak_mock": {
                    "identifier": "hak_mock",
                    "status": "enabled",
                    "metadata_ref": None,
                },
            },
            "model_metadata": {
                "ce": {
                    "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
                    "revision": "v2.0",
                    "license": "other",
                    "access_status": "open",
                    "usage_restriction": "colab_validation_only",
                    "approval_state": "not_approved",
                },
                "formo": {
                    "model_id": "formospeech/whisper-large-v3-taiwanese-hakka",
                    "revision": "main",
                    "license": "CC BY-NC 4.0",
                    "access_status": "gated",
                    "usage_restriction": "colab_validation_only",
                    "approval_state": "not_approved",
                },
            },
        }

    def test_valid_config_parses(self) -> None:
        config = parse_asr_config(self._valid_config_data())
        assert isinstance(config, AsrConfig)
        assert "zh-TW" in config.routes
        assert "hak" in config.routes

    def test_missing_routes_fail_closed(self) -> None:
        data = self._valid_config_data()
        del data["routes"]
        with pytest.raises(ConfigParseError, match="Missing required key 'routes'"):
            parse_asr_config(data)

    def test_missing_providers_fail_closed(self) -> None:
        data = self._valid_config_data()
        del data["providers"]
        with pytest.raises(ConfigParseError, match="Missing required key 'providers'"):
            parse_asr_config(data)

    def test_contradictory_ce_production_fail_closed(self) -> None:
        data = self._valid_config_data()
        data["model_metadata"]["ce"]["usage_restriction"] = "production"
        with pytest.raises(ConfigParseError, match="Contradictory state"):
            parse_asr_config(data)

    def test_contradictory_formo_production_fail_closed(self) -> None:
        data = self._valid_config_data()
        data["model_metadata"]["formo"]["usage_restriction"] = "production"
        with pytest.raises(ConfigParseError, match="Contradictory state"):
            parse_asr_config(data)

    def test_non_dict_input_fail_closed(self) -> None:
        with pytest.raises(ConfigParseError, match="must be a dict"):
            parse_asr_config("not a dict")

    def test_unknown_provider_status_fail_closed(self) -> None:
        data = self._valid_config_data()
        data["providers"]["aws_zh_tw_adapter"]["status"] = "unknown_status"
        with pytest.raises(ConfigParseError, match="Unknown provider status"):
            parse_asr_config(data)


# ─────────────────────────────────────────────────────────────────
# Convenience Error Builders
# ─────────────────────────────────────────────────────────────────
class TestErrorBuilders:
    def test_route_not_approved(self) -> None:
        err = make_route_not_approved_error("gate incomplete")
        assert err.category == AsrErrorCategory.ROUTE_NOT_APPROVED
        assert not err.retryable

    def test_unsupported_language(self) -> None:
        err = make_unsupported_language_error("en")
        assert err.category == AsrErrorCategory.UNSUPPORTED_LANGUAGE
        assert not err.retryable
