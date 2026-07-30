"""
遠端 ASR 推論端點 provider — SageMaker real-time endpoint。

用途：模型太大、需要 GPU，不可能跑在 chat Lambda 裡。實體模型託管在
SageMaker 端點上（見 terraform/asr_models.tf），後端只負責呼叫。

這樣一來備援鏈的兩種語意都有了實體對應：
- 「為了錯誤而備援」：主端點回 5xx／throttling／model-not-ready → 換備援端點。
- 「根據流量而備援」：本地 slot pool 限制單一端點的同時外呼數，滿了就溢流到
  備援端點；端點本身另有 target tracking autoscaling 向外擴充。

**誠實的限制**：真實網路呼叫送出後無法強制中斷。deadline 與 cancellation 只能在
送出前後檢查，並透過 botocore 的 read timeout 讓呼叫不會無限期掛住。這與
provider_base 的協作式取消是同一套約定，不是搶佔式取消。

boto3 延遲 import：模組匯入不應把 SDK 拉進來，也讓不需要 AWS 的測試能執行。

禁止依賴：handlers、HTTP、DB。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .concurrency import ModelSlotPool
from .config import (
    make_provider_failure_error,
    make_provider_invalid_response_error,
    make_provider_unavailable_error,
)
from .provider_base import ModelProviderBase, TypedAsrErrorSignal, guard
from .types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    Deadline,
    Language,
    TypedAsrError,
)

# 端點暫時性不可用的錯誤碼：這些換一個端點有機會成功，屬於可轉移。
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

# 逾時類例外的類別名稱。botocore 的逾時不帶 error code，只能看型別名稱。
_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {"ReadTimeoutError", "ConnectTimeoutError", "ConnectionClosedError"}
)


@dataclass(frozen=True)
class RemoteEndpointSpec:
    """遠端端點的呼叫參數。"""

    endpoint_name: str
    model_id: str
    revision: str
    region_name: str | None = None
    # 單次呼叫的 read timeout。設得比呼叫端 deadline 寬一點沒有意義，
    # 因為 adapter 的 postflight 會先判定逾期。
    read_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.endpoint_name.strip():
            raise ValueError("RemoteEndpointSpec.endpoint_name must be non-blank.")
        if not self.model_id.strip():
            raise ValueError("RemoteEndpointSpec.model_id must be non-blank.")
        if self.read_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("RemoteEndpointSpec timeouts must be positive.")


class SageMakerAsrProvider(ModelProviderBase):
    """
    呼叫 SageMaker real-time endpoint 的 ASR provider。

    送出的內容只有 Canonical Audio 的 PCM bytes 與必要的呼叫欄位；不含原始
    WAV/M4A bytes、token、prompt ID 或長者資料。
    """

    def __init__(
        self,
        provider_id: str,
        spec: RemoteEndpointSpec,
        slot_pool: ModelSlotPool,
        supported_languages: frozenset[Language],
        client_wait_seconds: float = 10.0,
        load_retry_cooldown_seconds: float = 60.0,
    ) -> None:
        if not supported_languages:
            raise ValueError("SageMakerAsrProvider requires at least one language.")
        super().__init__(
            provider_id=provider_id,
            slot_pool=slot_pool,
            handle_wait_seconds=client_wait_seconds,
            handle_name=f"{provider_id}:{spec.endpoint_name}",
            load_retry_cooldown_seconds=load_retry_cooldown_seconds,
        )
        self._spec = spec
        self._languages = supported_languages

    @property
    def endpoint_name(self) -> str:
        return self._spec.endpoint_name

    def _supports(self, language: Language) -> bool:
        return language in self._languages

    def _build_handle(self) -> Any:
        # 延遲 import：模組匯入時不把 boto3 拉進來。
        import boto3
        from botocore.config import Config

        return boto3.client(
            "sagemaker-runtime",
            region_name=self._spec.region_name,
            config=Config(
                read_timeout=self._spec.read_timeout_seconds,
                connect_timeout=self._spec.connect_timeout_seconds,
                # 重試交給備援鏈決定，SDK 不自行重試：否則同一個壞掉的端點會被
                # 重打數次，把 deadline 耗盡卻沒機會換備援。
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )

    def _run_inference(
        self,
        handle: Any,
        audio: CanonicalAudio,
        language: Language,
        cancellation: CancellationSignal,
        deadline: Deadline,
    ) -> str | None:
        # 送出前最後一次檢查：網路呼叫一旦送出就無法收回。
        guard(cancellation, deadline)

        # TODO: 推論容器的輸入契約尚未實作（terraform 需要 image URI 才能建立
        # 端點）。目前約定為：body 是 raw PCM s16le bytes，語言與取樣率走
        # CustomAttributes，回應是 {"text": "..."} 的 JSON。容器實作完成後
        # 必須與此處對齊。
        try:
            response = handle.invoke_endpoint(
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
            # 把 SDK 例外分類成領域錯誤，且不外洩原始訊息。
            raise TypedAsrErrorSignal(self._classify(exc)) from None

        guard(cancellation, deadline)

        return self._extract_text(response)

    # ── 錯誤分類與回應解析 ────────────────────────────────────────
    def _classify(self, exc: Exception) -> TypedAsrError:
        """把 SDK 例外映射成領域錯誤。訊息一律固定文案。"""
        name = type(exc).__name__

        if name in _TIMEOUT_EXCEPTION_NAMES:
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message=f"Endpoint call timed out in provider {self.provider_id!r}.",
                retryable=True,
            )

        code = _error_code(exc)
        if code in _TRANSIENT_ERROR_CODES:
            return make_provider_unavailable_error(
                f"Endpoint for provider {self.provider_id!r} is temporarily unavailable."
            )

        return make_provider_failure_error(
            f"Endpoint call failed in provider {self.provider_id!r}."
        )

    def _extract_text(self, response: Any) -> str | None:
        """
        從端點回應取出候選文字。

        結構不符一律視為無效回應，不猜測、不從其他欄位湊。
        """
        try:
            body = response["Body"].read()
        except Exception:
            raise TypedAsrErrorSignal(
                make_provider_invalid_response_error(
                    "Endpoint response body could not be read."
                )
            ) from None

        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            raise TypedAsrErrorSignal(
                make_provider_invalid_response_error(
                    "Endpoint response was not valid JSON."
                )
            ) from None

        if not isinstance(payload, dict):
            raise TypedAsrErrorSignal(
                make_provider_invalid_response_error(
                    "Endpoint response was not a JSON object."
                )
            ) from None

        # 不存在或非字串都交給骨架的 _normalize 判為無效回應。
        return payload.get("text")


def _error_code(exc: Exception) -> str | None:
    """取出 botocore ClientError 的錯誤碼；非 ClientError 回 None。"""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) else None
