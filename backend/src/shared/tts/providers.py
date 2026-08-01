"""TTS provider adapters；所有外部失敗都轉成穩定領域錯誤。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .config import ProviderConfig
from .types import (
    CancellationSignal,
    Deadline,
    HakkaDialect,
    Language,
    SynthesizedAudio,
    TtsErrorCategory,
    TtsTerminalResult,
    TypedTtsError,
)


class TtsProvider(Protocol):
    provider_id: str

    def synthesize(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> TtsTerminalResult: ...


class PollyTtsProvider:
    """AWS Polly adapter；voice 與 engine 完全由受控設定注入。"""

    def __init__(self, config: ProviderConfig, client: Any = None) -> None:
        self.provider_id = config.identifier
        self._config = config
        self._client = client

    def synthesize(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> TtsTerminalResult:
        guard = _guard(deadline, cancellation)
        if guard:
            return guard
        try:
            client = self._client
            if client is None:
                import boto3
                from botocore.config import Config

                client = boto3.client(
                    "polly",
                    config=Config(
                        connect_timeout=2,
                        read_timeout=8,
                        retries={"max_attempts": 1, "mode": "standard"},
                    ),
                )
                self._client = client
            response = client.synthesize_speech(
                Engine=self._config.engine,
                OutputFormat="mp3",
                Text=text,
                VoiceId=self._config.voice_id,
            )
            audio = response["AudioStream"].read()
        except Exception:
            return _provider_failure(self.provider_id)
        guard = _guard(deadline, cancellation)
        if guard:
            return guard
        return _audio_or_error(audio, self.provider_id)


class SageMakerTtsProvider:
    """remote-only TTS adapter；Lambda 不載入模型或聲紋。"""

    def __init__(self, config: ProviderConfig, client: Any = None) -> None:
        self.provider_id = config.identifier
        self._config = config
        self._client = client

    def synthesize(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> TtsTerminalResult:
        guard = _guard(deadline, cancellation)
        if guard:
            return guard
        payload: dict[str, str] = {
            "text": text,
            "language": language.value,
            "format": "mp3",
        }
        if dialect is not None:
            payload["dialect"] = dialect.value
        if self._config.speaker:
            payload["speaker"] = self._config.speaker
        try:
            client = self._client
            if client is None:
                import boto3
                from botocore.config import Config

                client = boto3.client(
                    "sagemaker-runtime",
                    config=Config(
                        connect_timeout=2,
                        read_timeout=8,
                        retries={"max_attempts": 1, "mode": "standard"},
                    ),
                )
                self._client = client
            response = client.invoke_endpoint(
                EndpointName=self._config.endpoint_name,
                ContentType="application/json",
                Accept="audio/mpeg",
                Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except Exception:
            return _provider_failure(self.provider_id)
        if response.get("ContentType") != "audio/mpeg":
            return TypedTtsError(
                TtsErrorCategory.INVALID_RESPONSE,
                f"TTS provider {self.provider_id!r} returned an invalid content type.",
                True,
            )
        try:
            audio = response["Body"].read()
        except Exception:
            return TypedTtsError(
                TtsErrorCategory.INVALID_RESPONSE,
                f"TTS provider {self.provider_id!r} returned unreadable audio.",
                True,
            )
        guard = _guard(deadline, cancellation)
        if guard:
            return guard
        return _audio_or_error(audio, self.provider_id)


class MockTtsProvider:
    """只供單元測試顯式注入，不會由 production composition 自動建立。"""

    def __init__(self, provider_id: str = "tts_mock", audio: bytes = b"ID3mock") -> None:
        self.provider_id = provider_id
        self._audio = audio

    def synthesize(
        self,
        text: str,
        language: Language,
        dialect: HakkaDialect | None,
        deadline: Deadline,
        cancellation: CancellationSignal,
    ) -> TtsTerminalResult:
        guard = _guard(deadline, cancellation)
        return guard or SynthesizedAudio(self._audio, self.provider_id)


def _guard(
    deadline: Deadline, cancellation: CancellationSignal
) -> TypedTtsError | None:
    if cancellation.is_triggered:
        return TypedTtsError(TtsErrorCategory.CANCELLED, "TTS cancelled.", False)
    if deadline.is_expired():
        return TypedTtsError(
            TtsErrorCategory.DEADLINE_EXCEEDED, "TTS deadline exceeded.", True
        )
    return None


def _provider_failure(provider_id: str) -> TypedTtsError:
    return TypedTtsError(
        TtsErrorCategory.PROVIDER_UNAVAILABLE,
        f"TTS provider {provider_id!r} is unavailable.",
        True,
    )


def _audio_or_error(audio: Any, provider_id: str) -> TtsTerminalResult:
    if not isinstance(audio, bytes) or not audio:
        return TypedTtsError(
            TtsErrorCategory.INVALID_RESPONSE,
            f"TTS provider {provider_id!r} returned invalid audio.",
            True,
        )
    return SynthesizedAudio(audio, provider_id)
