"""
ASR production composition root — 從環境設定組出可用的 AsrFacade。

這是唯一負責「決定實際建立哪些 provider 實例」的地方。router 只做路由決策，
provider 只做推論；把建構決策集中在這裡，才能讓未核准的模型連實例都不存在。

Lambda 的 warm start 會重用同一個 process，因此 facade 在 process 級快取：
模型 handle 得以跨請求重用，不必每次冷載。快取本身 thread-safe。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from dataclasses import dataclass

from .concurrency import ModelSlotPool
from .config import (
    AsrConfig,
    CE_MODEL_METADATA,
    ConcurrencyPolicy,
    ConfigParseError,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    parse_asr_config,
)
from .facade import AsrFacade
from .hak_mock import HakMockProvider
from .remote_endpoints import RemoteEndpointSpec, SageMakerAsrProvider
from .router import AsrRouter
from .telemetry import SafeTelemetryRecord
from .types import Language

# 環境變數鍵
ENV_CONFIG_JSON = "ASR_CONFIG_JSON"
# Lambda 執行環境本來就會提供 AWS_REGION；沒有時交給 boto3 自行解析。
ENV_AWS_REGION = "AWS_REGION"


# ─────────────────────────────────────────────────────────────────
# 模型 provider 註冊表
#
# 新增一個開源模型只需在這裡加一筆，不必修改 `_build_remote_model_provider`
# 的判斷邏輯。刻意不做成「設定檔指定任意類別路徑」：那等於讓 JSON 設定可以
# 載入任意程式碼，是安全風險。註冊表本身仍是程式碼變更，只是把「加模型」
# 限縮成單一、可讀的登記動作。
#
# remote-only 架構：Lambda 不可在 process 內載入或執行 ASR 模型。
# 模型必須只在 SageMaker Endpoint 執行。
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelProviderRegistration:
    """
    單一模型在遠端端點的建構方式。

    `languages` 是這個模型實際支援的語言，供 `remote_endpoints.py` 決定要不要
    收指定語言的音訊。
    """

    languages: frozenset[Language]
    build_remote: Callable[[str, RemoteEndpointSpec, ModelSlotPool], object]


def _build_ce_remote(
    provider_id: str, spec: RemoteEndpointSpec, slot_pool: ModelSlotPool
) -> object:
    return SageMakerAsrProvider(
        provider_id=provider_id,
        spec=spec,
        slot_pool=slot_pool,
        supported_languages=MODEL_PROVIDER_REGISTRY[CE_MODEL_METADATA.model_id].languages,
    )


def _build_formo_remote(
    provider_id: str, spec: RemoteEndpointSpec, slot_pool: ModelSlotPool
) -> object:
    return SageMakerAsrProvider(
        provider_id=provider_id,
        spec=spec,
        slot_pool=slot_pool,
        supported_languages=MODEL_PROVIDER_REGISTRY[FORMO_MODEL_METADATA.model_id].languages,
    )


# model_id → 建構方式。要新增第三個開源模型：
#   1. 在 remote_endpoints.py 寫一個 ModelProviderBase 子類別。
#   2. 在下面加一筆 ModelProviderRegistration。
# router 的核准判定、備援鏈、併發控制、遙測都不需要修改。
MODEL_PROVIDER_REGISTRY: dict[str, ModelProviderRegistration] = {
    CE_MODEL_METADATA.model_id: ModelProviderRegistration(
        languages=frozenset({Language.ZH_TW, Language.HAK}),
        build_remote=_build_ce_remote,
    ),
    FORMO_MODEL_METADATA.model_id: ModelProviderRegistration(
        languages=frozenset({Language.HAK}),
        build_remote=_build_formo_remote,
    ),
}


# ─────────────────────────────────────────────────────────────────
# Telemetry sink
# ─────────────────────────────────────────────────────────────────
class StdoutTelemetrySink:
    """
    把 Safe Telemetry 以單行 JSON 印到 stdout。

    刻意不依賴任何遙測 SDK：在 Lambda 中 stdout 本來就會被收集，
    在本機則直接可讀。要換成其他 sink 只需注入別的實作。
    """

    def emit(self, record: SafeTelemetryRecord) -> None:
        print(json.dumps({"asr_telemetry": record.to_dict()}, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────
# 預設設定 — 一律 fail closed
# ─────────────────────────────────────────────────────────────────
def default_config() -> AsrConfig:
    """
    沒有提供環境設定時使用的安全預設。

    行為（remote-only 架構）：
    - `hak` 走 `hak_mock`，備援為 `ce_remote`。mock 可用，CE 因未核准而被跳過。
    - `zh-TW` 走 `ce_remote`，備援為 `formo_remote`。兩者均未核准，
      因此 zh-TW 目前必然回 `route_not_approved`。

    也就是說：**預設狀態下只有客語 mock 能出結果**，其餘都 fail closed。
    要開通任何實體模型，必須在 `ASR_CONFIG_JSON` 明確填上 production gate。
    """
    return AsrConfig(
        routes={
            "hak": RouteConfig(
                route="hak_primary",
                provider_identifier="hak_mock",
                enabled=True,
                fallback_chain=("ce_remote",),
            ),
            "zh-TW": RouteConfig(
                route="zh_tw_primary",
                provider_identifier="ce_remote",
                enabled=True,
                fallback_chain=("formo_remote",),
            ),
        },
        providers={
            "hak_mock": ProviderConfig(
                identifier="hak_mock",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.MOCK,
            ),
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="taiwan_tongues_ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ai-elder-care-asr-ce",
                max_concurrent=4,
            ),
            "formo_remote": ProviderConfig(
                identifier="formo_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="formospeech_whisper_v3",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ai-elder-care-asr-formo",
                max_concurrent=2,
            ),
        },
        model_metadata={
            "taiwan_tongues_ce": CE_MODEL_METADATA,
            "formospeech_whisper_v3": FORMO_MODEL_METADATA,
        },
        formo_prompt_id_allowlist=frozenset(
            {
                "htia_sixian",
                "htia_hailu",
                "htia_dapu",
                "htia_raoping",
                "htia_zhaoan",
                "htia_nansixian",
            }
        ),
        concurrency=ConcurrencyPolicy(),
    )


def load_config() -> AsrConfig:
    """
    從 `ASR_CONFIG_JSON` 載入設定；未設定時用 `default_config()`。

    解析失敗一律 raise `ConfigParseError`，不退回預設值——否則 operator 打錯的
    設定會被靜默忽略，實際生效的東西與他以為的不同，比直接失敗更危險。
    """
    raw = os.environ.get(ENV_CONFIG_JSON)
    if not raw or not raw.strip():
        return default_config()
    try:
        data: Any = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigParseError(
            f"{ENV_CONFIG_JSON} is not valid JSON: {exc}. Fail closed."
        )
    return parse_asr_config(data)


# ─────────────────────────────────────────────────────────────────
# Provider registry
# ─────────────────────────────────────────────────────────────────
def build_provider_registry(config: AsrConfig) -> dict[str, object]:
    """
    依設定建立 provider 實例。

    只建立「有資格」的 provider：實體模型必須通過 production gate，否則連實例都
    不會存在。這與 router 的資格判定重複，是刻意的雙重防線——一層是決策，
    一層是「根本沒有東西可以被呼叫」。

    無法辨識的 provider 不會被建立；router 會因此判定不可用並回
    `route_not_approved`。

    remote-only 架構：只建立 mock 與 remote_model 兩種 provider。
    """
    registry: dict[str, object] = {}

    for provider_id, provider_config in config.providers.items():
        if provider_config.status is not ProviderStatus.ENABLED:
            continue

        instance = _build_provider(provider_id, provider_config, config)
        if instance is not None:
            registry[provider_id] = instance

    return registry


def _build_provider(
    provider_id: str,
    provider_config: ProviderConfig,
    config: AsrConfig,
) -> object | None:
    kind = provider_config.kind

    if kind is ProviderKind.MOCK:
        # 目前只有客語 mock 一種；其他 mock identifier 不建立實例。
        if provider_id == "hak_mock":
            return HakMockProvider()
        return None

    if kind is ProviderKind.REMOTE_MODEL:
        return _build_remote_model_provider(provider_id, provider_config, config)

    # 未知 kind — fail closed：不建立實例。
    return None


def _approved_metadata(
    provider_config: ProviderConfig, config: AsrConfig
) -> ModelMetadata | None:
    """取得已核准的 model metadata；未核准或查不到回 None。"""
    if not provider_config.metadata_ref:
        return None
    metadata = config.model_metadata.get(provider_config.metadata_ref)
    if metadata is None or not metadata.is_production_allowed:
        return None
    return metadata


def _build_remote_model_provider(
    provider_id: str,
    provider_config: ProviderConfig,
    config: AsrConfig,
) -> object | None:
    metadata = _approved_metadata(provider_config, config)
    if metadata is None or not provider_config.endpoint_name:
        return None

    # 未登記的 model_id 不建立實例：即使核准通過，程式不認得它就不能跑。
    registration = MODEL_PROVIDER_REGISTRY.get(metadata.model_id)
    if registration is None:
        return None

    spec = RemoteEndpointSpec(
        endpoint_name=provider_config.endpoint_name,
        model_id=metadata.model_id,
        revision=metadata.revision,
        region_name=os.environ.get(ENV_AWS_REGION) or None,
    )
    slot_pool = ModelSlotPool(
        provider_id=provider_id, capacity=provider_config.max_concurrent
    )
    return registration.build_remote(provider_id, spec, slot_pool)


# ─────────────────────────────────────────────────────────────────
# Facade 組裝與 process 級快取
# ─────────────────────────────────────────────────────────────────
def build_facade(
    config: AsrConfig | None = None,
    providers: dict[str, object] | None = None,
    telemetry_sink: object | None = None,
    clock: Callable[[], float] | None = None,
) -> AsrFacade:
    """組出一個 AsrFacade。參數皆可注入，供測試替換。"""
    resolved_config = config if config is not None else load_config()
    resolved_providers = (
        providers if providers is not None else build_provider_registry(resolved_config)
    )
    return AsrFacade(
        router=AsrRouter(resolved_config, providers=resolved_providers),
        telemetry_sink=telemetry_sink or StdoutTelemetrySink(),  # type: ignore[arg-type]
        clock=clock or time.monotonic,
    )


_facade_lock = threading.Lock()
_cached_facade: AsrFacade | None = None


def get_asr_facade() -> AsrFacade:
    """
    取得 process 共用的 AsrFacade（Lambda warm start 之間重用）。

    以 double-checked locking 保證只組裝一次；facade 本身可被多執行緒同時呼叫。
    """
    global _cached_facade

    facade = _cached_facade
    if facade is not None:
        return facade

    with _facade_lock:
        if _cached_facade is None:
            _cached_facade = build_facade()
        return _cached_facade


def reset_asr_facade() -> None:
    """清除快取。僅供測試與設定變更後重建使用。"""
    global _cached_facade
    with _facade_lock:
        _cached_facade = None
