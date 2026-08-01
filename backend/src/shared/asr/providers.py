"""ASR provider 協定、測試 mock 與 SageMaker adapter。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .config import (
    make_provider_failure_error,
    make_provider_invalid_response_error,
    make_provider_unavailable_error,
    make_route_not_approved_error,
)
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)


class AsrProvider(Protocol):
    """所有 provider 只接受已正規化的 CanonicalAudio。"""

    provider_id: str

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult: ...


_HAK_MOCK_TRANSCRIPT_TEXT = "客語測試轉錄結果"


class HakMockProvider:
    """不使用音訊內容或網路的決定性客語測試 provider。"""

    provider_id = "hak_mock"

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult:
        del audio, language, deadline, cancellation, context
        return Transcript(text=_HAK_MOCK_TRANSCRIPT_TEXT)


_TRANSIENT_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelNotReadyException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalFailure",
        "InternalServerException",
        "ModelError",
        "TooManyRequestsException",
    }
)
_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {"ReadTimeoutError", "ConnectTimeoutError", "ConnectionClosedError"}
)


@dataclass(frozen=True)
class RemoteEndpointSpec:
    """不含 request PII 的固定 endpoint 呼叫參數。"""

    endpoint_name: str
    model_id: str
    revision: str
    region_name: str | None = None
    read_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.endpoint_name.strip() or not self.model_id.strip():
            raise ValueError("Remote endpoint and model ID must be non-blank.")
        if self.read_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("Remote endpoint timeouts must be positive.")


class SageMakerAsrProvider:
    """直接呼叫 SageMaker；endpoint 的容量與擴縮由 AWS 管理。"""

    def __init__(
        self,
        provider_id: str,
        spec: RemoteEndpointSpec,
        supported_languages: frozenset[Language],
        client: Any = None,
    ) -> None:
        if not provider_id.strip() or not supported_languages:
            raise ValueError("ASR provider requires an ID and supported languages.")
        self.provider_id = provider_id
        self._spec = spec
        self._languages = supported_languages
        self._client = client

    @property
    def endpoint_name(self) -> str:
        return self._spec.endpoint_name

    def transcribe(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult:
        del context  # correlation ID 不得送進 provider。
        if not isinstance(audio, CanonicalAudio):
            return make_provider_invalid_response_error(
                "Provider requires CanonicalAudio input."
            )
        if language not in self._languages:
            return make_route_not_approved_error(
                f"Provider {self.provider_id!r} does not serve this language."
            )
        guard = _guard(deadline, cancellation)
        if guard is not None:
            return guard

        try:
            response = self._get_client().invoke_endpoint(
                EndpointName=self._spec.endpoint_name,
                ContentType="application/octet-stream",
                Accept="application/json",
                Body=audio.pcm_s16le,
                CustomAttributes=(
                    f"language={language.value};"
                    f"sample_rate_hz={audio.sample_rate_hz};"
                    f"channels={audio.channels}"
                ),
            )
        except Exception as exc:
            return self._classify(exc)

        guard = _guard(deadline, cancellation)
        if guard is not None:
            return guard
        return _extract_transcript(response)

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "sagemaker-runtime",
                region_name=self._spec.region_name,
                config=Config(
                    read_timeout=self._spec.read_timeout_seconds,
                    connect_timeout=self._spec.connect_timeout_seconds,
                    retries={"max_attempts": 1, "mode": "standard"},
                ),
            )
        return self._client

    def _classify(self, exc: Exception) -> TypedAsrError:
        if type(exc).__name__ in _TIMEOUT_EXCEPTION_NAMES:
            return TypedAsrError(
                AsrErrorCategory.DEADLINE_EXCEEDED,
                f"Endpoint call timed out in provider {self.provider_id!r}.",
                True,
            )
        if _error_code(exc) in _TRANSIENT_ERROR_CODES:
            return make_provider_unavailable_error(
                f"Provider {self.provider_id!r} is temporarily unavailable."
            )
        return make_provider_failure_error(
            f"Endpoint call failed in provider {self.provider_id!r}."
        )


def _guard(
    deadline: Deadline, cancellation: CancellationSignal
) -> TypedAsrError | None:
    if cancellation.is_triggered:
        return TypedAsrError(AsrErrorCategory.CANCELLED, "ASR cancelled.", False)
    if deadline.is_expired():
        return TypedAsrError(
            AsrErrorCategory.DEADLINE_EXCEEDED,
            "ASR deadline exceeded.",
            True,
        )
    return None


def _extract_transcript(response: Any) -> AsrTerminalResult:
    try:
        body = response["Body"].read()
        payload = json.loads(body)
    except Exception:
        return make_provider_invalid_response_error(
            "Endpoint response was not valid JSON."
        )
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return make_provider_invalid_response_error(
            "Endpoint response did not contain non-blank text."
        )
    return Transcript(text=text)


def _error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None
