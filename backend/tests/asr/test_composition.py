"""
Composition root 測試 — 預設設定必須 fail closed、provider 只在核准後才被建立。

驗證重點是「未核准的模型連實例都不存在」，這是 router 資格判定之外的第二道防線。
不載入真實模型、不連網。
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from src.shared.asr.composition import (
    ENV_CONFIG_JSON,
    StdoutTelemetrySink,
    build_facade,
    build_provider_registry,
    default_config,
    get_asr_facade,
    load_config,
    reset_asr_facade,
)
from src.shared.asr.config import (
    AccessStatus,
    ApprovalState,
    ConfigParseError,
    ModelMetadata,
    ModelProductionGate,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    UsageRestriction,
)
from src.shared.asr.remote_endpoints import SageMakerAsrProvider
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)

APPROVED_GATE = ModelProductionGate(
    colab_validation_passed=True,
    license_cleared=True,
    access_granted=True,
    quota_cleared=True,
    runtime_capacity_verified=True,
    approval_record_ref="docs/adr/asr-ce-production-approval.md",
)


def approve(metadata: ModelMetadata) -> ModelMetadata:
    """把 metadata 改成完全核准的版本。"""
    return ModelMetadata(
        model_id=metadata.model_id,
        revision=metadata.revision,
        license=metadata.license,
        access_status=AccessStatus.OPEN,
        usage_restriction=UsageRestriction.PRODUCTION,
        approval_state=ApprovalState.APPROVED,
        production_gate=APPROVED_GATE,
    )


@pytest.fixture(autouse=True)
def _clear_env_and_cache(monkeypatch: pytest.MonkeyPatch):
    """每個測試都從乾淨的環境與空快取開始。"""
    monkeypatch.delenv(ENV_CONFIG_JSON, raising=False)
    reset_asr_facade()
    yield
    reset_asr_facade()


def make_wav_bytes(duration_ms: int = 200) -> bytes:
    """產生一段最小的合法 WAV，供 facade 端到端呼叫使用。"""
    import struct

    sample_rate = 16_000
    frames = int(sample_rate * duration_ms / 1000)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


# ─────────────────────────────────────────────────────────────────
# 預設設定
# ─────────────────────────────────────────────────────────────────
def test_default_config_declares_both_languages() -> None:
    config = default_config()
    assert set(config.routes) == {"hak", "zh-TW"}
    assert config.routes["hak"].provider_order == ("hak_mock", "ce_remote")
    assert config.routes["zh-TW"].provider_order == ("ce_remote", "formo_remote")


def test_default_config_leaves_both_models_unapproved() -> None:
    """預設狀態下兩個候選模型都不得可用——Colab 驗證還沒做。"""
    config = default_config()
    for metadata in config.model_metadata.values():
        assert metadata.is_production_allowed is False



def test_default_registry_contains_no_local_models() -> None:
    """未核准 → 連實例都不該存在。remote-only 架構下只有 hak_mock。"""
    registry = build_provider_registry(default_config())
    assert set(registry) == {"hak_mock"}
    assert "ce_remote" not in registry
    assert "formo_remote" not in registry


def test_disabled_provider_is_not_instantiated() -> None:
    config = default_config()
    config.providers["hak_mock"] = type(config.providers["hak_mock"])(
        identifier="hak_mock",
        status=ProviderStatus.DISABLED,
        kind=config.providers["hak_mock"].kind,
    )
    assert "hak_mock" not in build_provider_registry(config)


# ─────────────────────────────────────────────────────────────────
# 核准後才建立實例
# ─────────────────────────────────────────────────────────────────
def test_approved_ce_model_is_instantiated_with_configured_capacity() -> None:
    config = default_config()
    config.model_metadata["taiwan_tongues_ce"] = approve(
        config.model_metadata["taiwan_tongues_ce"]
    )

    registry = build_provider_registry(config)

    assert isinstance(registry["ce_remote"], SageMakerAsrProvider)
    assert registry["ce_remote"].slot_stats.capacity == 4
    # 建立實例不等於載入模型：handle 必須維持延遲載入。
    assert registry["ce_remote"].is_loaded is False


def test_approved_formo_model_is_instantiated() -> None:
    """核准後 formo_remote 也能被建立。"""
    config = default_config()
    config.model_metadata["formospeech_whisper_v3"] = approve(
        config.model_metadata["formospeech_whisper_v3"]
    )

    registry = build_provider_registry(config)
    assert isinstance(registry["formo_remote"], SageMakerAsrProvider)
    assert registry["formo_remote"].slot_stats.capacity == 2


# ─────────────────────────────────────────────────────────────────
# 環境設定載入
# ─────────────────────────────────────────────────────────────────
def test_load_config_without_env_returns_default() -> None:
    config = load_config()
    assert set(config.routes) == {"hak", "zh-TW"}


def test_load_config_parses_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "routes": {
            "hak": {
                "route": "hak_only",
                "provider_identifier": "hak_mock",
                "enabled": True,
            }
        },
        "providers": {
            "hak_mock": {
                "identifier": "hak_mock",
                "status": "enabled",
                "kind": "mock",
            }
        },
        "model_metadata": {},
        "formo_prompt_id_allowlist": [
            "htia_sixian",
            "htia_hailu",
            "htia_dapu",
            "htia_raoping",
            "htia_zhaoan",
            "htia_nansixian",
        ],
        "concurrency": {"spill_wait_ms": 42},
    }
    monkeypatch.setenv(ENV_CONFIG_JSON, json.dumps(payload))

    config = load_config()

    assert set(config.routes) == {"hak"}
    assert config.concurrency.spill_wait_ms == 42


def test_malformed_env_json_fails_closed_instead_of_silently_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定打錯必須直接失敗，否則實際生效的東西與 operator 以為的不同。"""
    monkeypatch.setenv(ENV_CONFIG_JSON, "{not valid json")
    with pytest.raises(ConfigParseError):
        load_config()


def test_env_json_with_unknown_provider_reference_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "routes": {
            "hak": {
                "route": "hak_only",
                "provider_identifier": "ghost",
                "enabled": True,
            }
        },
        "providers": {},
        "model_metadata": {},
        "formo_prompt_id_allowlist": [
            "htia_sixian",
            "htia_hailu",
            "htia_dapu",
            "htia_raoping",
            "htia_zhaoan",
            "htia_nansixian",
        ],
    }
    monkeypatch.setenv(ENV_CONFIG_JSON, json.dumps(payload))
    with pytest.raises(ConfigParseError):
        load_config()


# ─────────────────────────────────────────────────────────────────
# Facade 組裝與快取
# ─────────────────────────────────────────────────────────────────
class CollectingSink:
    def __init__(self) -> None:
        self.records = []

    def emit(self, record) -> None:
        self.records.append(record)


def test_default_facade_serves_hak_via_mock_and_closes_zh_tw() -> None:
    """預設狀態的端到端行為：客語有結果，國語 fail closed。"""
    sink = CollectingSink()
    facade = build_facade(telemetry_sink=sink)
    audio = make_wav_bytes()

    hak_result = facade.recognize(
        audio,
        InputFormat.WAV,
        Language.HAK,
        Deadline.after(5.0, time.monotonic),
        CancellationSignal(),
        CorrelationContext(correlation_id="corr-hak"),
    )
    zh_result = facade.recognize(
        audio,
        InputFormat.WAV,
        Language.ZH_TW,
        Deadline.after(5.0, time.monotonic),
        CancellationSignal(),
        CorrelationContext(correlation_id="corr-zh"),
    )

    assert isinstance(hak_result, Transcript)
    assert isinstance(zh_result, TypedAsrError)
    assert zh_result.category is AsrErrorCategory.ROUTE_NOT_APPROVED

    # 每次呼叫恰一筆遙測，且 provider_id 反映實際服務者
    assert len(sink.records) == 2
    assert sink.records[0].provider_id == "hak_mock"
    assert sink.records[0].attempt_count == 1
    assert sink.records[0].failover_occurred is False


def test_facade_is_cached_per_process() -> None:
    first = get_asr_facade()
    second = get_asr_facade()
    assert first is second

    reset_asr_facade()
    assert get_asr_facade() is not first


def test_cached_facade_is_built_once_under_concurrency() -> None:
    """warm start 重用的前提是快取只組裝一次，否則模型會被載入多份。"""
    instances = []
    lock = threading.Lock()

    def worker() -> None:
        facade = get_asr_facade()
        with lock:
            instances.append(facade)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(instances) == 8
    assert all(instance is instances[0] for instance in instances)


def test_stdout_sink_emits_only_allowlisted_keys(capsys) -> None:
    from src.shared.asr.telemetry import TELEMETRY_ALLOWLIST_KEYS

    facade = build_facade(telemetry_sink=StdoutTelemetrySink())
    facade.recognize(
        make_wav_bytes(),
        InputFormat.WAV,
        Language.HAK,
        Deadline.after(5.0, time.monotonic),
        CancellationSignal(),
        CorrelationContext(correlation_id="corr-stdout"),
    )

    printed = json.loads(capsys.readouterr().out.strip())["asr_telemetry"]
    assert set(printed) <= TELEMETRY_ALLOWLIST_KEYS


# ─────────────────────────────────────────────────────────────────
# 遙測的併發／備援欄位（facade 層）
# ─────────────────────────────────────────────────────────────────
def test_telemetry_reports_failover_and_served_provider() -> None:
    """
    主力失敗、備援救回來時，遙測必須看得出來。

    這是新增三個觀測欄位的存在理由：沒有它們，這次請求與「一次就成功」
    在遙測上長得一模一樣。
    """
    from src.shared.asr.config import (
        AsrConfig,
               ConcurrencyPolicy,
        ProviderConfig,
        ProviderKind,
        RouteConfig,
    )
    from src.shared.asr.router import AsrRouter
    from src.shared.asr.telemetry import TELEMETRY_ALLOWLIST_KEYS

    class FailingProvider:
        provider_id = "primary_broken"

        def __init__(self) -> None:
            self.calls = 0

        def transcribe(self, audio, language, deadline, cancellation, context):
            self.calls += 1
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
                message="safe",
                retryable=True,
            )

    config = AsrConfig(
        routes={
            "hak": RouteConfig(
                route="hak_primary",
                provider_identifier="primary_broken",
                enabled=True,
                fallback_chain=("hak_mock",),
            )
        },
        providers={
            "primary_broken": ProviderConfig(
                identifier="primary_broken",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.MOCK,
            ),
            "hak_mock": ProviderConfig(
                identifier="hak_mock",
                status=ProviderStatus.ENABLED,
                kind=ProviderKind.MOCK,
            ),
        },
        model_metadata={},
        formo_prompt_id_allowlist=default_config().formo_prompt_id_allowlist,
        concurrency=ConcurrencyPolicy(spill_wait_ms=10),
    )

    broken = FailingProvider()
    sink = CollectingSink()
    facade = build_facade(
        config=config,
        providers={"primary_broken": broken},
        telemetry_sink=sink,
    )

    result = facade.recognize(
        make_wav_bytes(),
        InputFormat.WAV,
        Language.HAK,
        Deadline.after(5.0, time.monotonic),
        CancellationSignal(),
        CorrelationContext(correlation_id="corr-failover"),
    )

    assert isinstance(result, Transcript)
    assert broken.calls == 1

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.failover_occurred is True
    assert record.attempt_count == 2
    # provider_id 必須是實際服務者，不是設定中的主 provider
    assert record.provider_id == "hak_mock"
    assert record.route == "hak_primary"
    assert record.terminal_outcome == "success"
    assert record.queue_wait_ms >= 0
    assert set(record.to_dict()) <= TELEMETRY_ALLOWLIST_KEYS


# ─────────────────────────────────────────────────────────────────
# 模型 provider 註冊表
# ─────────────────────────────────────────────────────────────────
def test_registry_contains_exactly_the_two_known_models() -> None:
    """
    這個測試故意寫得很嚴格：新增或移除模型都必須動到這裡，

    確保「支援哪些模型」永遠有一份可讀、可追溯的清單，不會被默默改掉。
    """
    from src.shared.asr.composition import MODEL_PROVIDER_REGISTRY
    from src.shared.asr.config import CE_MODEL_METADATA, FORMO_MODEL_METADATA

    assert set(MODEL_PROVIDER_REGISTRY) == {
        CE_MODEL_METADATA.model_id,
        FORMO_MODEL_METADATA.model_id,
    }


def test_unregistered_model_id_does_not_get_a_remote_instance() -> None:
    """
    核准通過但程式不認得這個模型時，必須靜靜地不建立實例，

    而不是報錯或猜一個實作——這是新增模型前的安全網。
    """
    from src.shared.asr.composition import build_provider_registry

    config = default_config()
    config.model_metadata["taiwan_tongues_ce"] = approve(
        config.model_metadata["taiwan_tongues_ce"]
    )
    # 竄改成一個沒登記過的 model_id，模擬「設定填了新模型但程式還沒認得它」。
    tampered = config.model_metadata["taiwan_tongues_ce"]
    config.model_metadata["taiwan_tongues_ce"] = ModelMetadata(
        model_id="someone/unregistered-model",
        revision=tampered.revision,
        license=tampered.license,
        access_status=tampered.access_status,
        usage_restriction=tampered.usage_restriction,
        approval_state=tampered.approval_state,
        production_gate=tampered.production_gate,
    )

    assert "ce_remote" not in build_provider_registry(config)


def test_third_model_can_be_added_by_registering_it_without_touching_router() -> None:
    """
    示範新增第三個開源模型的最小改動：只需一筆註冊，其餘管線不變。

    這裡直接把假模型登記進 MODEL_PROVIDER_REGISTRY，證明 composition 之外
    （router 的核准判定、備援鏈、併發控制）完全不需要修改。
    """
    from src.shared.asr.composition import (
        MODEL_PROVIDER_REGISTRY,
        ModelProviderRegistration,
        build_provider_registry,
    )
    from src.shared.asr.config import ProviderKind

    class ThirdPartyProvider:
        provider_id = "third_party_remote"

        def transcribe(self, audio, language, deadline, cancellation, context):
            return Transcript(text="第三方模型的假辨識結果")

    def build_third_party(provider_id, spec, slot_pool):
        return ThirdPartyProvider()

    fake_model_id = "someone/third-party-asr"
    MODEL_PROVIDER_REGISTRY[fake_model_id] = ModelProviderRegistration(
        languages=frozenset({Language.ZH_TW}),
        build_remote=build_third_party,
    )
    try:
        config = default_config()
        config.model_metadata["third_party"] = approve(
            ModelMetadata(
                model_id=fake_model_id,
                revision="v1",
                license="apache-2.0",
                access_status=AccessStatus.OPEN,
                usage_restriction=UsageRestriction.COLAB_VALIDATION_ONLY,
                approval_state=ApprovalState.NOT_APPROVED,
            )
        )
        config.providers["third_party_remote"] = ProviderConfig(
            identifier="third_party_remote",
            status=ProviderStatus.ENABLED,
            metadata_ref="third_party",
            kind=ProviderKind.REMOTE_MODEL,
            endpoint_name="third-party-endpoint",
        )

        registry = build_provider_registry(config)

        assert isinstance(registry["third_party_remote"], ThirdPartyProvider)
    finally:
        # 不污染其他測試：註冊表是模組級可變狀態。
        del MODEL_PROVIDER_REGISTRY[fake_model_id]
