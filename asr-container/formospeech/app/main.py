"""SageMaker real-time endpoint 的 serving 層。

契約見 docs/asr/sagemaker-inference-contract.md：`GET /ping` 回健康狀態，
`POST /invocations` 收 raw PCM S16LE、回 `{"text": ...}`，錯誤一律非 2xx 且 body 與
log 都不得帶出音訊、逐字稿、prompt ID、模型路徑或 traceback。

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

from .config import ContainerConfig
from .contract import ContractError, validate_request
from .transcriber import TranscriptionError

logger = logging.getLogger("formospeech")

# SageMaker 以這個 header 傳遞 InvokeEndpoint 的 CustomAttributes。
CUSTOM_ATTRIBUTES_HEADER = "X-Amzn-SageMaker-Custom-Attributes"


def _error(status_code: int, code: str) -> JSONResponse:
    """統一的錯誤回應；只回穩定代碼，不回細節。"""
    return JSONResponse(status_code=status_code, content={"error": code})


def create_app(
    config: ContainerConfig,
    transcriber_factory: Callable[[ContainerConfig], Any],
) -> FastAPI:
    """建立 app；`transcriber_factory` 注入以便測試不必載入 GPU 模型。"""
    state: dict[str, Any] = {"transcriber": None, "ready": False}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # 在 /ping 開始回 200 之前就把模型載進 GPU，避免 SageMaker 判定健康後
        # 第一個真實 request 卻要等冷載入。
        started = time.monotonic()
        state["transcriber"] = transcriber_factory(config)
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

        body = await request.body()
        try:
            validate_request(body, request.headers.get(CUSTOM_ATTRIBUTES_HEADER), config)
        except ContractError as exc:
            return _error(400, exc.code)

        started = time.monotonic()
        try:
            # 推論是同步阻塞；丟到 threadpool 才不會卡住 event loop，
            # 否則辨識期間 SageMaker 的 /ping 會逾時而把 instance 判為不健康。
            text = await run_in_threadpool(state["transcriber"].transcribe, body)
        except TranscriptionError:
            # 只記錄音訊長度與耗時；音訊與逐字稿內容不進 log。
            logger.error(
                "transcription failed (bytes=%d, elapsed=%.1fs)",
                len(body),
                time.monotonic() - started,
            )
            return _error(500, "transcription_failed")
        except Exception as exc:
            # 只留例外類別名；traceback 與訊息可能含音訊或模型路徑，依契約不得寫進 log。
            logger.error("unexpected transcription error (%s)", type(exc).__name__)
            return _error(500, "transcription_failed")

        logger.info(
            "transcribed bytes=%d elapsed=%.1fs", len(body), time.monotonic() - started
        )
        return JSONResponse(status_code=200, content={"text": text})

    return app
