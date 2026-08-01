"""ASR provider 協定、測試 mock、Transcribe 與 SageMaker adapters。"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Coroutine, Protocol, TypeVar

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

AMAZON_TRANSCRIBE_PROVIDER_ID = "amazon_transcribe_zh_tw"
TRANSCRIBE_LANGUAGE_CODE = "zh-TW"
TRANSCRIBE_MEDIA_ENCODING = "pcm"
TRANSCRIBE_SAMPLE_RATE_HZ = 16_000
# 100 ms、mono 16 kHz、16-bit PCM；分塊不改變 canonical 音訊內容。
TRANSCRIBE_AUDIO_CHUNK_BYTES = 3_200
_TRANSCRIBE_CONTROL_POLL_SECONDS = 0.01
_TRANSCRIBE_STREAM_CLOSE_TIMEOUT_SECONDS = 0.25
_TRANSCRIBE_TRANSIENT_ERROR_CODES = frozenset(
    {
        "InternalFailure",
        "InternalFailureException",
        "InternalServerException",
        "LimitExceededException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)
_TRANSCRIBE_TRANSIENT_EXCEPTION_NAMES = frozenset(
    {
        "AwsCrtError",
        "ConnectionClosedError",
        "LimitExceededException",
        "ServiceUnavailableException",
        "ThrottlingException",
    }
)


class _StreamingCancelled(Exception):
    """內部控制流程：呼叫端已取消。"""


class _StreamingDeadlineExceeded(Exception):
    """內部控制流程：呼叫端 deadline 已到期。"""


class _StreamingInvalidResponse(Exception):
    """內部控制流程：Transcribe stream 缺少必要介面。"""


class AmazonTranscribeAsrProvider:
    """以 Amazon Transcribe Streaming 辨識 canonical 台灣華語 PCM。"""

    def __init__(
        self,
        provider_id: str = AMAZON_TRANSCRIBE_PROVIDER_ID,
        region_name: str | None = None,
        client: Any = None,
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("ASR provider requires a non-blank ID.")
        if region_name is not None and not region_name.strip():
            raise ValueError("AWS Region must be non-blank when provided.")
        self.provider_id = provider_id
        self._region_name = region_name
        self._client = client

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
        if language is not Language.ZH_TW:
            return make_route_not_approved_error(
                f"Provider {self.provider_id!r} does not serve this language."
            )
        guard = _guard(deadline, cancellation)
        if guard is not None:
            return guard

        try:
            text = _run_coroutine(
                _run_with_controls(
                    self._transcribe_stream(audio), deadline, cancellation
                )
            )
        except _StreamingCancelled:
            return TypedAsrError(
                AsrErrorCategory.CANCELLED,
                "ASR cancelled during provider invocation.",
                False,
            )
        except _StreamingDeadlineExceeded:
            return TypedAsrError(
                AsrErrorCategory.DEADLINE_EXCEEDED,
                "ASR deadline exceeded during provider invocation.",
                True,
            )
        except _StreamingInvalidResponse:
            return make_provider_invalid_response_error(
                "Transcribe returned an invalid stream response."
            )
        except asyncio.CancelledError:
            return make_provider_failure_error(
                f"Streaming call failed in provider {self.provider_id!r}."
            )
        except Exception as exc:
            return self._classify(exc)

        guard = _guard(deadline, cancellation)
        if guard is not None:
            return guard
        if not text.strip():
            return make_provider_invalid_response_error(
                "Transcribe returned no final non-blank transcript."
            )
        return Transcript(text=text)

    async def _transcribe_stream(self, audio: CanonicalAudio) -> str:
        client = self._get_client()
        stream = await client.start_stream_transcription(
            language_code=TRANSCRIBE_LANGUAGE_CODE,
            media_sample_rate_hz=TRANSCRIBE_SAMPLE_RATE_HZ,
            media_encoding=TRANSCRIBE_MEDIA_ENCODING,
        )
        input_stream = getattr(stream, "input_stream", None)
        output_stream = getattr(stream, "output_stream", None)
        if input_stream is None or output_stream is None:
            # SDK 若只建立輸入半邊，也必須在拒絕畸形 response 前收斂它。
            end_stream = getattr(input_stream, "end_stream", None)
            if callable(end_stream):
                try:
                    await asyncio.wait_for(
                        end_stream(),
                        timeout=_TRANSCRIBE_STREAM_CLOSE_TIMEOUT_SECONDS,
                    )
                except BaseException:
                    pass
            raise _StreamingInvalidResponse()

        final_segments: list[str] = []

        async def send_audio() -> None:
            send_failed = False
            try:
                for offset in range(
                    0, len(audio.pcm_s16le), TRANSCRIBE_AUDIO_CHUNK_BYTES
                ):
                    await input_stream.send_audio_event(
                        audio_chunk=audio.pcm_s16le[
                            offset : offset + TRANSCRIBE_AUDIO_CHUNK_BYTES
                        ]
                    )
            except BaseException:
                send_failed = True
                raise
            finally:
                try:
                    # 正常、錯誤、deadline 或 cancellation 都關閉雙向 stream。
                    await asyncio.wait_for(
                        input_stream.end_stream(),
                        timeout=_TRANSCRIBE_STREAM_CLOSE_TIMEOUT_SECONDS,
                    )
                except BaseException:
                    # 清理錯誤不可蓋掉原本的取消／傳送錯誤；正常路徑則回報失敗。
                    if not send_failed:
                        raise _StreamingInvalidResponse() from None

        async def receive_transcripts() -> None:
            try:
                async for event in output_stream:
                    transcript = getattr(event, "transcript", None)
                    results = getattr(transcript, "results", ())
                    for result in results or ():
                        # `is_partial` 必須明確為 False；未知事件不視為 final。
                        if getattr(result, "is_partial", True) is not False:
                            continue
                        alternatives = getattr(result, "alternatives", ())
                        if not alternatives:
                            continue
                        segment = getattr(alternatives[0], "transcript", None)
                        if isinstance(segment, str) and segment.strip():
                            final_segments.append(segment.strip())
            except TypeError as exc:
                raise _StreamingInvalidResponse() from exc

        sender = asyncio.create_task(send_audio())
        receiver = asyncio.create_task(receive_transcripts())
        try:
            await asyncio.gather(sender, receiver)
        finally:
            # gather 遇到單邊錯誤不會自動取消另一邊，必須顯式收斂所有工作。
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
        return "".join(final_segments)

    def _get_client(self) -> Any:
        if self._client is None:
            # 套件只在實際外呼時載入，避免測試與非 ASR Lambda 啟動時引入 SDK。
            from amazon_transcribe.client import TranscribeStreamingClient

            if self._region_name is None:
                raise RuntimeError("AWS Region is required for Transcribe Streaming.")
            self._client = TranscribeStreamingClient(region=self._region_name)
        return self._client

    def _classify(self, exc: Exception) -> TypedAsrError:
        if (
            _error_code(exc) in _TRANSCRIBE_TRANSIENT_ERROR_CODES
            or type(exc).__name__ in _TRANSCRIBE_TRANSIENT_EXCEPTION_NAMES
            or (
                isinstance(exc, (ConnectionError, OSError))
                and not isinstance(exc, TimeoutError)
            )
        ):
            return make_provider_unavailable_error(
                f"Provider {self.provider_id!r} is temporarily unavailable."
            )
        if (
            isinstance(exc, TimeoutError)
            or type(exc).__name__ in _TIMEOUT_EXCEPTION_NAMES
        ):
            return TypedAsrError(
                AsrErrorCategory.DEADLINE_EXCEEDED,
                f"Streaming call timed out in provider {self.provider_id!r}.",
                True,
            )
        return make_provider_failure_error(
            f"Streaming call failed in provider {self.provider_id!r}."
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


_T = TypeVar("_T")


def _run_coroutine(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """從同步 provider 介面執行 async SDK，也支援呼叫執行緒已有 event loop。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    results: list[_T] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            results.append(asyncio.run(coroutine))
        except BaseException as exc:  # 需把 CancelledError 帶回同步邊界分類。
            errors.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]


async def _run_with_controls(
    coroutine: Coroutine[Any, Any, _T],
    deadline: Deadline,
    cancellation: CancellationSignal,
) -> _T:
    """在整段 async stream 期間持續執行 deadline 與取消檢查。"""
    task = asyncio.create_task(coroutine)
    while not task.done():
        if cancellation.is_triggered:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise _StreamingCancelled()
        remaining = deadline.remaining_seconds()
        if remaining <= 0:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise _StreamingDeadlineExceeded()
        await asyncio.wait(
            {task}, timeout=min(_TRANSCRIBE_CONTROL_POLL_SECONDS, remaining)
        )
    return await task


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
