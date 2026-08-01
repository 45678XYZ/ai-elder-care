"""SageMaker real-time endpoint 的 serving 層。

契約見 docs/tts/sagemaker-inference-contract.md：`GET /ping` 回健康狀態，
`POST /invocations` 收 UTF-8 JSON、回 `audio/mpeg` 的 MP3 bytes，錯誤一律非 2xx
且 body 與 log 都不得帶出文字、音訊、模型路徑或 traceback。

request 欄位驗證放在 contract.py，這裡只負責 HTTP 與錯誤映射。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .audio import AudioEncodeError, encode_mp3
from .config import BREEZYVOICE_SAMPLE_RATE, ContainerConfig, resolve_speaker_dir
from .contract import ContractError, parse_payload
from .synthesizer import SynthesisError

logger = logging.getLogger("breezyvoice")


def _error(status_code: int, code: str) -> JSONResponse:
    """統一的錯誤回應；只回穩定代碼，不回細節。"""
    return JSONResponse(status_code=status_code, content={"error": code})


def create_app(
    config: ContainerConfig,
    synthesizer_factory: Callable[[ContainerConfig], Any],
) -> FastAPI:
    """建立 app；`synthesizer_factory` 注入以便測試不必載入 GPU 模型。"""
    state: dict[str, Any] = {"synthesizer": None, "ready": False}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # 在 /ping 開始回 200 之前就把模型載進 GPU，避免 SageMaker 判定健康後
        # 第一個真實 request 卻要等冷載入。
        started = time.monotonic()
        state["synthesizer"] = synthesizer_factory(config)
        state["ready"] = True
        logger.info("model loaded in %.1fs", time.monotonic() - started)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ping")
    def ping() -> Response:
        return Response(status_code=200 if state["ready"] else 503)

    @app.post("/invocations")
    async def invocations(request: Request) -> Response:
        if not state["ready"]:
            return _error(503, "model_not_ready")

        raw = await request.body()
        try:
            parsed = parse_payload(raw, config)
        except ContractError as exc:
            return _error(400, exc.code)

        try:
            speaker_dir = resolve_speaker_dir(config, parsed.speaker)
        except ValueError:
            return _error(400, "unknown_speaker")

        started = time.monotonic()
        try:
            # 推論與編碼都是同步阻塞；丟到 threadpool 才不會卡住 event loop，
            # 否則合成期間 SageMaker 的 /ping 會逾時而把 instance 判為不健康。
            waveform = await run_in_threadpool(
                state["synthesizer"].synthesize, parsed.text, speaker_dir
            )
            audio = await run_in_threadpool(encode_mp3, waveform, BREEZYVOICE_SAMPLE_RATE)
        except (SynthesisError, AudioEncodeError):
            # 只記錄輸入長度與耗時；文字與音訊內容不進 log。
            logger.error(
                "synthesis failed (chars=%d, elapsed=%.1fs)",
                len(parsed.text),
                time.monotonic() - started,
            )
            return _error(500, "synthesis_failed")
        except Exception as exc:
            # 只留例外類別名；traceback 與訊息可能含文字或模型路徑，依契約不得寫進 log。
            logger.error("unexpected synthesis error (%s)", type(exc).__name__)
            return _error(500, "synthesis_failed")

        logger.info(
            "synthesized chars=%d bytes=%d elapsed=%.1fs",
            len(parsed.text),
            len(audio),
            time.monotonic() - started,
        )
        return Response(content=audio, media_type="audio/mpeg")

    return app
