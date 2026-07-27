"""Bedrock 呼叫層：Converse、structured outputs、embedding 與錯誤分類。

所有模型呼叫都經過這裡，理由有三個：

1. **錯誤要分成 retryable 與 permanent**。batch worker 依此決定「throw 讓 SQS 重投」還是
   「把 session 標 failed 並 ack」。分不清就會出現無限重試或永久資料遺失兩種極端。
2. **structured outputs 有降級路徑**。Bedrock 的 structured outputs 是伺服器端 grammar
   約束解碼，schema 首次使用要編譯 grammar 並快取 24 小時；若 botocore 版本或所選模型不
   支援 `outputConfig`，這裡會自動退回「prompt 指引 JSON + 後驗證」並在 metadata 標記，
   讓上層仍能完成工作，同時留下可觀測的訊號。
3. **embedding 供應者要可抽換**。Titan v2 與 Cohere v3 的請求格式與批次上限不同，
   模型與維度走環境變數（決策 B：比賽當天比對後定案），程式不寫死。
"""

from collections.abc import Sequence
from typing import Any, Protocol
import json
import logging
import os
import random
import re
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

logger = logging.getLogger(__name__)

# 對話模型的 modelId。預設用 Anthropic 目前在 Bedrock 上的旗艦模型，並走 global
# cross-Region inference profile（`global.` 前綴）：台灣沒有 Bedrock 區域，global CRIS
# 會把請求路由到可服務的區域，可用性與吞吐都比綁單一區域好。
#
# 想固定區域時把前綴換掉即可，例如 `us.anthropic.claude-opus-4-6-v1:0`；
# 想省成本時換 Sonnet／Haiku。一律由環境變數決定，程式不寫死。
DEFAULT_MODEL_ID = "global.anthropic.claude-opus-4-6-v1:0"
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)

# 呼叫層的重試次數（含首次）；boto3 內建重試只處理連線層，模型層的節流另外算
MAX_ATTEMPTS = int(os.environ.get("BEDROCK_MAX_ATTEMPTS", "4"))
BASE_DELAY_SECONDS = float(os.environ.get("BEDROCK_BASE_DELAY_SECONDS", "0.5"))
MAX_DELAY_SECONDS = float(os.environ.get("BEDROCK_MAX_DELAY_SECONDS", "8"))

# 節流與暫時性故障：重試有意義
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "ServiceQuotaExceededException",
    }
)

# 請求本身或權限有問題：重試只會浪費配額
PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "SerializationException",
        "ModelErrorException",
    }
)

_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)

_runtime_client = None


class BedrockError(Exception):
    """Bedrock 呼叫失敗。"""


class RetryableBedrockError(BedrockError):
    """暫時性失敗；batch worker 應 throw 讓 SQS 重投。"""


class PermanentBedrockError(BedrockError):
    """請求或權限問題；batch worker 應把 session 標為 failed 並 ack。"""


def get_runtime_client():
    """取得或初始化 bedrock-runtime client（warm start 重用）。"""
    global _runtime_client
    if _runtime_client is None:
        _runtime_client = boto3.client(
            "bedrock-runtime",
            config=Config(
                retries={"max_attempts": 2, "mode": "standard"},
                # 萃取請求的輸出較長，預設 60 秒讀取逾時容易在冷啟動時誤判失敗
                read_timeout=int(os.environ.get("BEDROCK_READ_TIMEOUT", "120")),
                connect_timeout=10,
            ),
        )
    return _runtime_client


def reset_runtime_client() -> None:
    """清掉快取的 client；測試在切換 stub 時使用。"""
    global _runtime_client
    _runtime_client = None


def classify_client_error(error: ClientError) -> BedrockError:
    """把 botocore 例外轉成 retryable／permanent 兩類。"""
    code = error.response.get("Error", {}).get("Code", "")
    message = error.response.get("Error", {}).get("Message", str(error))
    if code in RETRYABLE_ERROR_CODES:
        return RetryableBedrockError(f"{code}: {message}")
    if code in PERMANENT_ERROR_CODES:
        return PermanentBedrockError(f"{code}: {message}")
    # 未知錯誤碼保守視為可重試，但留下告警以便補進分類表
    logger.warning("未分類的 Bedrock 錯誤碼，暫視為可重試：code=%s", code)
    return RetryableBedrockError(f"{code}: {message}")


def _sleep_backoff(attempt: int) -> None:
    delay = min(BASE_DELAY_SECONDS * (2**attempt), MAX_DELAY_SECONDS)
    # 加抖動避免多個 worker 同時重試造成第二波節流
    time.sleep(delay * (0.5 + random.random() / 2))


def _invoke_with_retry(operation, /, **kwargs) -> dict[str, Any]:
    last_error: BedrockError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return operation(**kwargs)
        except ClientError as exc:
            error = classify_client_error(exc)
            if isinstance(error, PermanentBedrockError):
                raise error from exc
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                _sleep_backoff(attempt)
    raise last_error or RetryableBedrockError("Bedrock 呼叫失敗且無錯誤資訊")


def _output_config(schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """structured outputs 的請求片段。

    形狀依 Bedrock structured outputs 文件（Converse 的 `outputConfig.textFormat`）。
    模型或 botocore 版本不支援時由 `converse` 降級處理，不在這裡判斷。
    """
    return {
        "textFormat": {
            "type": "json_schema",
            "jsonSchema": {"name": schema_name, "schema": schema, "strict": True},
        }
    }


def converse(
    prompt: str,
    *,
    system: str | None = None,
    model_id: str | None = None,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "Output",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    client=None,
) -> tuple[str, dict[str, Any]]:
    """呼叫 Converse 並回傳 `(輸出文字, metadata)`。

    帶 `json_schema` 時優先使用 structured outputs；不受支援則降級為 prompt 指引 JSON，
    並在 metadata 的 `structured_output` 標記 False 供觀測。
    """
    runtime = client or get_runtime_client()
    resolved_model = model_id or BEDROCK_MODEL_ID

    request: dict[str, Any] = {
        "modelId": resolved_model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        request["system"] = [{"text": system}]

    structured = json_schema is not None
    if structured:
        request["outputConfig"] = _output_config(json_schema, schema_name)

    started = time.monotonic()
    try:
        response = _invoke_with_retry(runtime.converse, **request)
    except (ParamValidationError, PermanentBedrockError) as exc:
        if not structured or not _looks_like_unsupported_structured_output(exc):
            raise exc if isinstance(exc, BedrockError) else PermanentBedrockError(str(exc)) from exc
        logger.warning(
            "此環境不支援 structured outputs，降級為 prompt 指引 JSON：model_id=%s reason=%s",
            resolved_model,
            exc,
        )
        request.pop("outputConfig", None)
        request["messages"][0]["content"][0]["text"] = (
            f"{prompt}\n\n請只輸出符合上述 JSON Schema 的 JSON 物件，不要加任何說明文字或程式碼圍欄。"
        )
        structured = False
        response = _invoke_with_retry(runtime.converse, **request)

    text = _first_text_block(response)
    metadata = {
        "model_id": resolved_model,
        "structured_output": structured,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "usage": response.get("usage", {}),
        "stop_reason": response.get("stopReason"),
    }
    return text, metadata


def _looks_like_unsupported_structured_output(exc: Exception) -> bool:
    text = str(exc)
    return "outputConfig" in text or "textFormat" in text or "json_schema" in text


def _first_text_block(response: dict[str, Any]) -> str:
    content = (response.get("output") or {}).get("message", {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    return ""


def extract_json(text: str) -> dict[str, Any]:
    """從模型輸出中取出 JSON 物件。

    即使走 structured outputs 也保留這層：降級路徑與外層圍欄（```json）都需要它。
    解不出來回空 dict，由呼叫端決定是重試還是丟棄。
    """
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    for candidate in (stripped, _JSON_BLOCK_PATTERN.search(stripped)):
        if candidate is None:
            continue
        raw = candidate if isinstance(candidate, str) else candidate.group(0)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
    logger.warning("模型輸出無法解析為 JSON 物件")
    return {}


def converse_json(
    prompt: str,
    json_schema: dict[str, Any],
    *,
    system: str | None = None,
    model_id: str | None = None,
    schema_name: str = "Output",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    client=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """呼叫 Converse 並回傳解析後的 JSON 物件。

    整份 JSON 解不開屬暫時性問題（模型偶發輸出污染），拋 retryable 讓上層重試。
    """
    text, metadata = converse(
        prompt,
        system=system,
        model_id=model_id,
        json_schema=json_schema,
        schema_name=schema_name,
        max_tokens=max_tokens,
        temperature=temperature,
        client=client,
    )
    data = extract_json(text)
    if not data:
        raise RetryableBedrockError("模型未回傳可解析的 JSON 物件")
    return data, metadata


# -----------------------------------------------------------------------------
# Embedding
# -----------------------------------------------------------------------------


class EmbeddingProvider(Protocol):
    """embedding 供應者介面。

    測試注入固定向量的 stub，就能離線驗證 depth score 與檢索聚合；
    正式環境走 Bedrock。索引維度在建立時固定，因此 `dimension` 是介面的一部分。
    """

    model_id: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class BedrockEmbeddingProvider:
    """以 Bedrock 產生句向量。

    Titan 一次只吃一段文字、Cohere 一次可送多段，差異收在這裡；
    上層只看得到 `embed_documents`／`embed_query`。
    """

    # Cohere 單次請求的文字數上限
    COHERE_BATCH_SIZE = 96

    def __init__(self, model_id: str, dimension: int, client=None):
        self.model_id = model_id
        self.dimension = dimension
        self._client = client

    @property
    def client(self):
        return self._client or get_runtime_client()

    @property
    def is_cohere(self) -> bool:
        return "cohere" in self.model_id

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(list(texts), input_type="search_document")

    def embed_query(self, text: str) -> list[float]:
        vectors = self._embed([text], input_type="search_query")
        return vectors[0]

    def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []
        if self.is_cohere:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), self.COHERE_BATCH_SIZE):
                batch = texts[start : start + self.COHERE_BATCH_SIZE]
                payload = {"texts": batch, "input_type": input_type}
                body = self._invoke(payload)
                embeddings = body.get("embeddings")
                if isinstance(embeddings, dict):
                    embeddings = embeddings.get("float") or []
                vectors.extend(embeddings)
            return vectors

        vectors = []
        for text in texts:
            body = self._invoke({"inputText": text, "dimensions": self.dimension, "normalize": True})
            vectors.append(body["embedding"])
        return vectors

    def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = _invoke_with_retry(
            self.client.invoke_model,
            modelId=self.model_id,
            body=json.dumps(payload),
            contentType="application/json",
            accept="application/json",
        )
        raw = response["body"].read()
        return json.loads(raw)
