"""Amazon Transcribe Streaming adapter 的契約、控制流程與安全錯誤測試。"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from src.shared.asr.providers import (
    TRANSCRIBE_AUDIO_CHUNK_BYTES,
    AmazonTranscribeAsrProvider,
)
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
)

AUDIO = CanonicalAudio(
    pcm_s16le=b"\x01\x02" * (TRANSCRIBE_AUDIO_CHUNK_BYTES + 1),
    sample_rate_hz=16_000,
    channels=1,
    sample_width_bits=16,
    duration_ms=400,
    input_format=InputFormat.WAV,
)
CONTEXT = CorrelationContext("corr-transcribe")
_ORIGINAL_SOCKET = socket.socket


@pytest.fixture(autouse=True)
def _allow_only_asyncio_loopback_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """允許 asyncio 在 Windows 建 self-pipe，外部位址仍維持阻斷。"""

    class LoopbackOnlySocket(_ORIGINAL_SOCKET):
        def connect(self, address):
            host = address[0] if isinstance(address, tuple) and address else None
            if host not in {"127.0.0.1", "::1"}:
                raise OSError("External network is disabled in ASR tests.")
            return super().connect(address)

    monkeypatch.setattr(socket, "socket", LoopbackOnlySocket)


def transcript_event(*results: object) -> object:
    return SimpleNamespace(transcript=SimpleNamespace(results=list(results)))


def result_event(text: str, *, partial: bool) -> object:
    alternative = SimpleNamespace(transcript=text)
    return SimpleNamespace(is_partial=partial, alternatives=[alternative])


class FakeInputStream:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.ended = False

    async def send_audio_event(self, *, audio_chunk: bytes) -> None:
        self.chunks.append(audio_chunk)

    async def end_stream(self) -> None:
        self.ended = True


class FakeOutputStream:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        return self._events.pop(0)


class FakeClient:
    def __init__(
        self,
        events: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = list(events or [])
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.input_stream = FakeInputStream()

    async def start_stream_transcription(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            input_stream=self.input_stream,
            output_stream=FakeOutputStream(self.events),
        )


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("secret transcript and endpoint")
        self.response = {"Error": {"Code": code}}


class ConnectionClosedError(Exception):
    pass


class ReadTimeoutError(Exception):
    pass


def run(
    provider: AmazonTranscribeAsrProvider,
    **overrides: object,
):
    values = {
        "audio": AUDIO,
        "language": Language.ZH_TW,
        "deadline": Deadline.after(2, time.monotonic),
        "cancellation": CancellationSignal(),
        "context": CONTEXT,
    }
    values.update(overrides)
    return provider.transcribe(**values)  # type: ignore[arg-type]


def test_streams_fixed_zh_tw_pcm_chunks_and_uses_only_final_results() -> None:
    client = FakeClient(
        [
            transcript_event(result_event("忽略中的文字", partial=True)),
            transcript_event(result_event("第一段", partial=False)),
            transcript_event(result_event("第二段", partial=False)),
        ]
    )
    result = run(AmazonTranscribeAsrProvider(client=client))

    assert isinstance(result, Transcript)
    assert result.text == "第一段第二段"
    assert client.calls == [
        {
            "language_code": "zh-TW",
            "media_sample_rate_hz": 16_000,
            "media_encoding": "pcm",
        }
    ]
    assert client.input_stream.chunks == [
        AUDIO.pcm_s16le[:TRANSCRIBE_AUDIO_CHUNK_BYTES],
        AUDIO.pcm_s16le[
            TRANSCRIBE_AUDIO_CHUNK_BYTES : TRANSCRIBE_AUDIO_CHUNK_BYTES * 2
        ],
        AUDIO.pcm_s16le[TRANSCRIBE_AUDIO_CHUNK_BYTES * 2 :],
    ]
    assert client.input_stream.ended is True


def test_partial_only_or_blank_final_is_invalid_response() -> None:
    partial_only = FakeClient(
        [transcript_event(result_event("不應輸出", partial=True))]
    )
    blank_final = FakeClient([transcript_event(result_event("   ", partial=False))])

    assert run(
        AmazonTranscribeAsrProvider(client=partial_only)
    ).category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE
    assert run(
        AmazonTranscribeAsrProvider(client=blank_final)
    ).category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_hakka_is_rejected_without_starting_stream() -> None:
    client = FakeClient()
    result = run(
        AmazonTranscribeAsrProvider(client=client), language=Language.HAK
    )

    assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED
    assert client.calls == []


def test_preflight_cancel_or_deadline_never_starts_stream() -> None:
    client = FakeClient()
    cancellation = CancellationSignal()
    cancellation.trigger()

    cancelled = run(
        AmazonTranscribeAsrProvider(client=client), cancellation=cancellation
    )
    expired = run(
        AmazonTranscribeAsrProvider(client=client),
        deadline=Deadline.after(-1, time.monotonic),
    )

    assert cancelled.category is AsrErrorCategory.CANCELLED
    assert expired.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert client.calls == []


def test_missing_stream_interface_is_invalid_response() -> None:
    class InvalidClient:
        async def start_stream_transcription(self, **kwargs):
            del kwargs
            return object()

    result = run(AmazonTranscribeAsrProvider(client=InvalidClient()))
    assert result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE


def test_missing_output_stream_closes_existing_input_stream() -> None:
    class InvalidClient:
        def __init__(self) -> None:
            self.input_stream = FakeInputStream()

        async def start_stream_transcription(self, **kwargs):
            del kwargs
            return SimpleNamespace(input_stream=self.input_stream)

    client = InvalidClient()
    result = run(AmazonTranscribeAsrProvider(client=client))

    assert result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE
    assert client.input_stream.ended is True


@pytest.mark.parametrize(
    "code",
    ["ThrottlingException", "LimitExceededException", "ServiceUnavailableException"],
)
def test_transient_service_errors_allow_ce_fallback(code: str) -> None:
    result = run(
        AmazonTranscribeAsrProvider(client=FakeClient(error=ClientError(code)))
    )

    assert result.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    assert "secret" not in result.message


def test_connection_closed_is_unavailable_but_timeout_is_terminal() -> None:
    connection_closed = run(
        AmazonTranscribeAsrProvider(
            client=FakeClient(error=ConnectionClosedError("secret"))
        )
    )
    read_timeout = run(
        AmazonTranscribeAsrProvider(
            client=FakeClient(error=ReadTimeoutError("secret"))
        )
    )
    timeout = run(
        AmazonTranscribeAsrProvider(client=FakeClient(error=TimeoutError("secret")))
    )

    assert connection_closed.category is AsrErrorCategory.PROVIDER_UNAVAILABLE
    assert read_timeout.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert timeout.category is AsrErrorCategory.DEADLINE_EXCEEDED


def test_unknown_error_is_safe_provider_failure() -> None:
    result = run(
        AmazonTranscribeAsrProvider(
            client=FakeClient(error=RuntimeError("secret transcript"))
        )
    )

    assert result.category is AsrErrorCategory.PROVIDER_FAILURE
    assert "secret transcript" not in result.message


@pytest.mark.parametrize("blocked_part", ["sender", "output"])
def test_deadline_cancels_a_blocked_stream(blocked_part: str) -> None:
    blocker_started = threading.Event()

    class BlockingInputStream(FakeInputStream):
        async def send_audio_event(self, *, audio_chunk: bytes) -> None:
            del audio_chunk
            blocker_started.set()
            await asyncio.Event().wait()

    class BlockingOutputStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            blocker_started.set()
            await asyncio.Event().wait()

    class BlockingClient:
        def __init__(self) -> None:
            self.input_stream = (
                BlockingInputStream()
                if blocked_part == "sender"
                else FakeInputStream()
            )

        async def start_stream_transcription(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                input_stream=self.input_stream,
                output_stream=(
                    BlockingOutputStream()
                    if blocked_part == "output"
                    else FakeOutputStream([])
                ),
            )

    client = BlockingClient()
    result = run(
        AmazonTranscribeAsrProvider(client=client),
        deadline=Deadline.after(0.05, time.monotonic),
    )

    assert blocker_started.is_set()
    assert result.category is AsrErrorCategory.DEADLINE_EXCEEDED
    assert client.input_stream.ended is True


def test_cancellation_stops_a_blocked_output_stream() -> None:
    cancellation = CancellationSignal()

    class BlockingOutputStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    class BlockingClient:
        def __init__(self) -> None:
            self.input_stream = FakeInputStream()

        async def start_stream_transcription(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                input_stream=self.input_stream,
                output_stream=BlockingOutputStream(),
            )

    client = BlockingClient()
    timer = threading.Timer(0.03, cancellation.trigger)
    timer.start()
    try:
        result = run(
            AmazonTranscribeAsrProvider(client=client),
            cancellation=cancellation,
        )
    finally:
        timer.cancel()

    assert result.category is AsrErrorCategory.CANCELLED
    assert client.input_stream.ended is True


def test_receiver_failure_cancels_sender_and_closes_input_stream() -> None:
    class BlockingInputStream(FakeInputStream):
        async def send_audio_event(self, *, audio_chunk: bytes) -> None:
            del audio_chunk
            await asyncio.Event().wait()

    class InvalidOutputStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise TypeError("invalid event stream")

    class Client:
        def __init__(self) -> None:
            self.input_stream = BlockingInputStream()

        async def start_stream_transcription(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                input_stream=self.input_stream,
                output_stream=InvalidOutputStream(),
            )

    client = Client()
    result = run(AmazonTranscribeAsrProvider(client=client))

    assert result.category is AsrErrorCategory.PROVIDER_INVALID_RESPONSE
    assert client.input_stream.ended is True


def test_postflight_cancellation_never_exposes_final_transcript() -> None:
    cancellation = CancellationSignal()
    client = FakeClient(
        [transcript_event(result_event("不得外露的文字", partial=False))]
    )

    class CancellingInputStream(FakeInputStream):
        async def end_stream(self) -> None:
            await super().end_stream()
            cancellation.trigger()

    client.input_stream = CancellingInputStream()
    result = run(
        AmazonTranscribeAsrProvider(client=client), cancellation=cancellation
    )

    assert result.category is AsrErrorCategory.CANCELLED
    assert "不得外露的文字" not in result.message


def test_sync_adapter_works_when_caller_thread_has_an_event_loop() -> None:
    client = FakeClient(
        [transcript_event(result_event("同步邊界", partial=False))]
    )
    provider = AmazonTranscribeAsrProvider(client=client)

    async def invoke_from_async_caller():
        return run(provider)

    result = asyncio.run(invoke_from_async_caller())
    assert isinstance(result, Transcript)
    assert result.text == "同步邊界"
