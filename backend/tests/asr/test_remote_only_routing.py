"""
遠端唯一 ASR 路由測試基線 — 驗證 remote-only 安全行為。

本測試模組證明：
- 預設安全狀態僅允許 `hak_mock`
- 合法遠端路由只有 `ce_remote` 與 `formo_remote`
- 未完整設定或未核准的遠端路由必須回傳 `route_not_approved`，不可回落到本機模型
- endpoint 缺失、gate 關閉、未知 provider、所有 fallback 失敗時都必須 fail closed
- 沒有任何情況會呼叫本機推論

禁止依賴：handlers、HTTP、DB、AWS SDK、真實模型。
"""
from __future__ import annotations

import json
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.shared.asr.composition import (
    ENV_CONFIG_JSON,
    build_facade,
    build_provider_registry,
    default_config,
    reset_asr_facade,
)
from src.shared.asr.config import (
    AccessStatus,
    ApprovalState,
    AsrConfig,
       CE_MODEL_METADATA,
    ConcurrencyPolicy,
    ConfigParseError,
    FORMO_MODEL_METADATA,
    ModelMetadata,
    ModelProductionGate,
    ProviderConfig,
    ProviderKind,
    ProviderStatus,
    RouteConfig,
    UsageRestriction,
)
from src.shared.asr.router import AsrRouter
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# 共用輔助
# ─────────────────────────────────────────────────────────────────
FORMO_ALLOWLIST = frozenset(
    {
        "htia_sixian",
        "htia_hailu",
        "htia_dapu",
        "htia_raoping",
        "htia_zhaoan",
        "htia_nansixian",
    }
)

APPROVED_GATE = ModelProductionGate(
    colab_validation_passed=True,
    license_cleared=True,
    access_granted=True,
    quota_cleared=True,
    runtime_capacity_verified=True,
    approval_record_ref="docs/adr/asr-model-validation.md",
)


def approved_metadata(base: ModelMetadata) -> ModelMetadata:
    """把 metadata 改成完全核准的版本。"""
    return ModelMetadata(
        model_id=base.model_id,
        revision=base.revision,
        license=base.license,
        access_status=AccessStatus.OPEN,
        usage_restriction=UsageRestriction.PRODUCTION,
        approval_state=ApprovalState.APPROVED,
        production_gate=APPROVED_GATE,
    )


def make_audio() -> CanonicalAudio:
    return CanonicalAudio(
        pcm_s16le=b"\x00\x00" * 160,
        sample_rate_hz=16_000,
        channels=1,
        sample_width_bits=16,
        duration_ms=10,
        input_format=InputFormat.WAV,
    )


def deadline_in(seconds: float) -> Deadline:
    return Deadline.after(seconds, time.monotonic)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_CONFIG_JSON, raising=False)
    reset_asr_facade()
    yield
    reset_asr_facade()


# ─────────────────────────────────────────────────────────────────
# 假 provider — 記錄是否被呼叫
# ─────────────────────────────────────────────────────────────────
class SpyProvider:
    """可追蹤呼叫次數的假 provider。"""

    def __init__(self, provider_id: str, result=None) -> None:
        self.provider_id = provider_id
        self._result = result or Transcript(text="spy-result")
        self.calls = 0

    def transcribe(self, audio, language, deadline, cancellation, context):
        self.calls += 1
        return self._result


class LocalModelSpy(SpyProvider):
    """模擬本機模型 — 如果被呼叫代表 fail-closed 失效。"""

    def __init__(self, provider_id: str = "ce_local") -> None:
        super().__init__(provider_id, Transcript(text="本機模型不該被呼叫"))


# ─────────────────────────────────────────────────────────────────
# remote-only 設定建構輔助
# ─────────────────────────────────────────────────────────────────
def make_remote_only_config(
    *,
    routes: dict[str, RouteConfig] | None = None,
    providers: dict[str, ProviderConfig] | None = None,
    metadata: dict[str, ModelMetadata] | None = None,
    concurrency: ConcurrencyPolicy | None = None,
) -> AsrConfig:
    """建構 remote-only 安全設定。"""
    default_routes = {
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
    }
    default_providers = {
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
    }
    default_metadata = {
        "taiwan_tongues_ce": approved_metadata(CE_MODEL_METADATA),
        "formospeech_whisper_v3": approved_metadata(FORMO_MODEL_METADATA),
    }
    return AsrConfig(
        routes=routes or default_routes,
        providers=providers or default_providers,
        model_metadata=metadata or default_metadata,
        formo_prompt_id_allowlist=FORMO_ALLOWLIST,
        concurrency=concurrency or ConcurrencyPolicy(spill_wait_ms=50),
    )


# ═════════════════════════════════════════════════════════════════
# 1. 預設安全狀態：僅允許 hak_mock
# ═════════════════════════════════════════════════════════════════
class TestDefaultSafeState:
    """無 AWS 設定時只能使用 mock ASR，不會使用模型。"""

    def test_default_hak_only_mock_works(self) -> None:
        """預設 hak 走 hak_mock 並成功。"""
        config = default_config()
        registry = build_provider_registry(config)
        router = AsrRouter(config, providers=registry)

        result = router.route(
            make_audio(), Language.HAK,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="safe-hak"),
        )
        assert isinstance(result, Transcript)

    def test_default_zh_tw_fails_closed(self) -> None:
        """預設 zh-TW 因 AWS gate 不完整而 fail closed。"""
        config = default_config()
        registry = build_provider_registry(config)
        router = AsrRouter(config, providers=registry)

        result = router.route(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="safe-zh"),
        )
        assert isinstance(result, TypedAsrError)
        assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_default_registry_has_no_local_model_instances(self) -> None:
        """預設不建立任何本機模型實例。"""
        registry = build_provider_registry(default_config())
        for provider_id in registry:
            assert "local" not in provider_id

    def test_default_registry_has_no_remote_model_instances(self) -> None:
        """預設不建立任何遠端模型實例（因為未核准）。"""
        registry = build_provider_registry(default_config())
        for provider_id in registry:
            assert "remote" not in provider_id


# ═════════════════════════════════════════════════════════════════
# 2. remote-only 路由：ce_remote 與 formo_remote
# ═════════════════════════════════════════════════════════════════
class TestRemoteOnlyRouting:
    """驗證 remote-only 設定下的路由正確性。"""

    def test_zh_tw_routes_to_ce_remote(self) -> None:
        """zh-TW 主力是 ce_remote。"""
        ce_spy = SpyProvider("ce_remote")
        config = make_remote_only_config()
        router = AsrRouter(config, providers={
            "hak_mock": SpyProvider("hak_mock"),
            "ce_remote": ce_spy,
            "formo_remote": SpyProvider("formo_remote"),
        })

        result = router.route(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="remote-zh"),
        )
        assert isinstance(result, Transcript)
        assert ce_spy.calls == 1

    def test_hak_primary_is_mock_fallback_is_ce_remote(self) -> None:
        """hak 主力是 hak_mock，備援是 ce_remote。"""
        config = make_remote_only_config()
        outcome = AsrRouter(config, providers={
            "hak_mock": SpyProvider("hak_mock"),
            "ce_remote": SpyProvider("ce_remote"),
            "formo_remote": SpyProvider("formo_remote"),
        }).route_detailed(
            make_audio(), Language.HAK,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="remote-hak"),
        )
        assert isinstance(outcome.result, Transcript)
        assert outcome.served_provider_id == "hak_mock"
        assert outcome.attempt_count == 1


# ═════════════════════════════════════════════════════════════════
# 3. Fail-closed 行為：各種缺失/錯誤情境
# ═════════════════════════════════════════════════════════════════
class TestFailClosed:
    """未完整設定或未核准必須 fail closed，不可回落本機模型。"""

    def test_endpoint_missing_fails_closed(self) -> None:
        """遠端 provider 缺少 endpoint_name → 不建立實例 → route_not_approved。"""
        config = make_remote_only_config(
            routes={
                "zh-TW": RouteConfig(
                    route="zh_tw_primary",
                    provider_identifier="ce_remote",
                    enabled=True,
                ),
            },
            providers={
                "ce_remote": ProviderConfig(
                    identifier="ce_remote",
                    status=ProviderStatus.ENABLED,
                    metadata_ref="taiwan_tongues_ce",
                    kind=ProviderKind.REMOTE_MODEL,
                    endpoint_name=None,  # 缺失
                    max_concurrent=1,
                ),
            },
        )
        registry = build_provider_registry(config)
        assert "ce_remote" not in registry

        router = AsrRouter(config, providers=registry)
        result = router.route(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="no-endpoint"),
        )
        assert isinstance(result, TypedAsrError)
        assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED

    def test_gate_not_approved_fails_closed(self) -> None:
        """production gate 未通過 → 不建立實例 → route_not_approved。"""
        config = make_remote_only_config(
            metadata={
                "taiwan_tongues_ce": CE_MODEL_METADATA,  # 未核准
                "formospeech_whisper_v3": FORMO_MODEL_METADATA,  # 未核准
            },
        )
        registry = build_provider_registry(config)
        assert "ce_remote" not in registry
        assert "formo_remote" not in registry

    def test_unknown_provider_kind_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未知 kind 在解析階段就 fail closed。"""
        payload = {
            "routes": {"hak": {"route": "hak", "provider_identifier": "x", "enabled": True}},
            "providers": {"x": {"identifier": "x", "status": "enabled", "kind": "quantum_asr"}},
            "model_metadata": {},
            "formo_prompt_id_allowlist": list(FORMO_ALLOWLIST),
        }
        monkeypatch.setenv(ENV_CONFIG_JSON, json.dumps(payload))
        from src.shared.asr.composition import load_config
        with pytest.raises(ConfigParseError):
            load_config()

    def test_all_fallback_fail_returns_last_error(self) -> None:
        """所有 provider 都失敗時，回傳最後一個的錯誤。"""
        failing_ce = SpyProvider(
            "ce_remote",
            TypedAsrError(
                category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
                message="ce down", retryable=True,
            ),
        )
        failing_formo = SpyProvider(
            "formo_remote",
            TypedAsrError(
                category=AsrErrorCategory.PROVIDER_FAILURE,
                message="formo down", retryable=True,
            ),
        )
        config = make_remote_only_config()
        router = AsrRouter(config, providers={
            "hak_mock": SpyProvider("hak_mock"),
            "ce_remote": failing_ce,
            "formo_remote": failing_formo,
        })

        outcome = router.route_detailed(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="all-fail"),
        )
        assert isinstance(outcome.result, TypedAsrError)
        assert outcome.result.category is AsrErrorCategory.PROVIDER_FAILURE
        assert outcome.attempt_count == 2
        assert outcome.failover_occurred is True

    def test_disabled_route_fails_closed(self) -> None:
        """路由被停用 → route_not_approved。"""
        config = make_remote_only_config(
            routes={
                "zh-TW": RouteConfig(
                    route="zh_tw_primary",
                    provider_identifier="ce_remote",
                    enabled=False,
                ),
            },
        )
        router = AsrRouter(config)
        result = router.route(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="disabled-route"),
        )
        assert isinstance(result, TypedAsrError)
        assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED


# ═════════════════════════════════════════════════════════════════
# 4. 本機推論不可被呼叫
# ═════════════════════════════════════════════════════════════════
class TestNoLocalInference:
    """測試必須證明沒有任何情況會呼叫本機推論。"""

    def test_local_model_provider_never_called_in_default(self) -> None:
        """預設設定中注入 local spy，證明永遠不被呼叫。"""
        local_spy = LocalModelSpy("ce_local")
        config = default_config()
        router = AsrRouter(config, providers={
            "hak_mock": SpyProvider("hak_mock"),
            "ce_remote": SpyProvider("ce_remote", TypedAsrError(
                category=AsrErrorCategory.ROUTE_NOT_APPROVED,
                message="unapproved", retryable=False,
            )),
            "formo_remote": SpyProvider("formo_remote", TypedAsrError(
                category=AsrErrorCategory.ROUTE_NOT_APPROVED,
                message="unapproved", retryable=False,
            )),
            "ce_local": local_spy,
        })

        # hak — 走 mock 不觸碰 local
        router.route(
            make_audio(), Language.HAK,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="no-local-1"),
        )
        assert local_spy.calls == 0

    def test_local_model_not_in_remote_only_registry(self) -> None:
        """remote-only 設定只宣告 mock 與 remote_model kind 的 provider。"""
        config = make_remote_only_config()
        for pid, pc in config.providers.items():
            assert pc.kind in (ProviderKind.MOCK, ProviderKind.REMOTE_MODEL), (
                f"provider {pid} should be MOCK or REMOTE_MODEL in remote-only config"
            )

    def test_local_model_kind_never_built_in_remote_only(self) -> None:
        """即使設定混入未知 kind，未核准也不建立實例。"""
        # remote-only 架構下 ProviderKind 只有 MOCK 和 REMOTE_MODEL，
        # 不存在 LOCAL_MODEL。未核准的 REMOTE_MODEL 也不會建立實例。
        config = make_remote_only_config(
            metadata={
                "taiwan_tongues_ce": CE_MODEL_METADATA,  # 未核准
                "formospeech_whisper_v3": FORMO_MODEL_METADATA,  # 未核准
            },
        )
        registry = build_provider_registry(config)
        # 只有 hak_mock 被建立
        assert set(registry) == {"hak_mock"}

    def test_fallback_to_remote_not_local_on_primary_failure(self) -> None:
        """主力遠端失敗時，備援也是遠端，不會回落到本機。"""
        failing_ce = SpyProvider(
            "ce_remote",
            TypedAsrError(
                category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
                message="down", retryable=True,
            ),
        )
        formo_spy = SpyProvider("formo_remote")
        config = make_remote_only_config()
        router = AsrRouter(config, providers={
            "hak_mock": SpyProvider("hak_mock"),
            "ce_remote": failing_ce,
            "formo_remote": formo_spy,
        })

        outcome = router.route_detailed(
            make_audio(), Language.ZH_TW,
            deadline_in(5.0), CancellationSignal(),
            CorrelationContext(correlation_id="fallback-remote"),
        )
        assert isinstance(outcome.result, Transcript)
        assert outcome.served_provider_id == "formo_remote"
        assert formo_spy.calls == 1


# ═════════════════════════════════════════════════════════════════
# 5. Property-based tests — 路由安全性質
# ═════════════════════════════════════════════════════════════════
_ALLOWED_REMOTE_PROVIDERS = frozenset({"hak_mock", "ce_remote", "formo_remote"})


@settings(max_examples=50)
@given(
    lang=st.sampled_from([Language.ZH_TW, Language.HAK]),
    ce_status=st.sampled_from([ProviderStatus.ENABLED, ProviderStatus.DISABLED]),
    formo_status=st.sampled_from([ProviderStatus.ENABLED, ProviderStatus.DISABLED]),
    route_enabled=st.booleans(),
)
def test_property_remote_only_never_calls_local(
    lang: Language,
    ce_status: ProviderStatus,
    formo_status: ProviderStatus,
    route_enabled: bool,
) -> None:
    """
    Property: 無論設定組合如何，remote-only 路由永遠不會呼叫本機模型 provider。

    任何結果要麼是 Transcript（來自 mock 或 remote），要麼是 TypedAsrError
    （fail closed），不會有 local inference。
    """
    local_spy = LocalModelSpy("ce_local")

    providers_cfg = {
        "hak_mock": ProviderConfig(
            identifier="hak_mock", status=ProviderStatus.ENABLED, kind=ProviderKind.MOCK,
        ),
        "ce_remote": ProviderConfig(
            identifier="ce_remote", status=ce_status,
            metadata_ref="taiwan_tongues_ce", kind=ProviderKind.REMOTE_MODEL,
            endpoint_name="ep-ce", max_concurrent=1,
        ),
        "formo_remote": ProviderConfig(
            identifier="formo_remote", status=formo_status,
            metadata_ref="formospeech_whisper_v3", kind=ProviderKind.REMOTE_MODEL,
            endpoint_name="ep-formo", max_concurrent=1,
        ),
    }

    routes_cfg = {
        lang.value: RouteConfig(
            route=f"{lang.value}_route",
            provider_identifier="hak_mock" if lang is Language.HAK else "ce_remote",
            enabled=route_enabled,
            fallback_chain=("ce_remote",) if lang is Language.HAK else ("formo_remote",),
        )
    }

    config = AsrConfig(
        routes=routes_cfg,
        providers=providers_cfg,
        model_metadata={
            "taiwan_tongues_ce": approved_metadata(CE_MODEL_METADATA),
            "formospeech_whisper_v3": approved_metadata(FORMO_MODEL_METADATA),
        },
        formo_prompt_id_allowlist=FORMO_ALLOWLIST,
        concurrency=ConcurrencyPolicy(spill_wait_ms=10),
    )

    # 注入 local spy — 不該被呼叫
    router = AsrRouter(config, providers={
        "hak_mock": SpyProvider("hak_mock"),
        "ce_remote": SpyProvider("ce_remote"),
        "formo_remote": SpyProvider("formo_remote"),
        "ce_local": local_spy,
    })

    router.route(
        make_audio(), lang,
        deadline_in(2.0), CancellationSignal(),
        CorrelationContext(correlation_id="prop-test"),
    )

    assert local_spy.calls == 0, "本機模型不應在 remote-only 設定下被呼叫"


@settings(max_examples=50)
@given(
    lang=st.sampled_from([Language.ZH_TW, Language.HAK]),
)
def test_property_unapproved_remote_always_fails_closed(lang: Language) -> None:
    """
    Property: 未核准的 remote provider 永遠回傳 route_not_approved。
    """
    config = AsrConfig(
        routes={
            lang.value: RouteConfig(
                route=f"{lang.value}_route",
                provider_identifier="ce_remote",
                enabled=True,
            ),
        },
        providers={
            "ce_remote": ProviderConfig(
                identifier="ce_remote",
                status=ProviderStatus.ENABLED,
                metadata_ref="taiwan_tongues_ce",
                kind=ProviderKind.REMOTE_MODEL,
                endpoint_name="ep-ce",
            ),
        },
        model_metadata={
            "taiwan_tongues_ce": CE_MODEL_METADATA,  # 未核准
        },
        formo_prompt_id_allowlist=FORMO_ALLOWLIST,
    )

    # 不注入實例 — build_provider_registry 也不會建
    router = AsrRouter(config, providers={})
    result = router.route(
        make_audio(), lang,
        deadline_in(2.0), CancellationSignal(),
        CorrelationContext(correlation_id="prop-unapproved"),
    )
    assert isinstance(result, TypedAsrError)
    assert result.category is AsrErrorCategory.ROUTE_NOT_APPROVED


# ═════════════════════════════════════════════════════════════════
# 6. ASR_CONFIG_JSON remote-only 設定端到端
# ═════════════════════════════════════════════════════════════════
class TestRemoteOnlyConfigJson:
    """驗證以 JSON 設定建構 remote-only facade 的端到端行為。"""

    def _remote_only_json(self) -> str:
        """產生一份完整合法的 remote-only 設定 JSON。"""
        return json.dumps({
            "routes": {
                "hak": {
                    "route": "hak_primary",
                    "provider_identifier": "hak_mock",
                    "enabled": True,
                    "fallback_chain": ["ce_remote"],
                },
                "zh-TW": {
                    "route": "zh_tw_primary",
                    "provider_identifier": "ce_remote",
                    "enabled": True,
                    "fallback_chain": ["formo_remote"],
                },
            },
            "providers": {
                "hak_mock": {
                    "identifier": "hak_mock",
                    "status": "enabled",
                    "kind": "mock",
                },
                "ce_remote": {
                    "identifier": "ce_remote",
                    "status": "enabled",
                    "kind": "remote_model",
                    "metadata_ref": "taiwan_tongues_ce",
                    "endpoint_name": "ai-elder-care-asr-ce",
                    "max_concurrent": 4,
                },
                "formo_remote": {
                    "identifier": "formo_remote",
                    "status": "enabled",
                    "kind": "remote_model",
                    "metadata_ref": "formospeech_whisper_v3",
                    "endpoint_name": "ai-elder-care-asr-formo",
                    "max_concurrent": 2,
                },
            },
            "model_metadata": {
                "taiwan_tongues_ce": {
                    "model_id": CE_MODEL_METADATA.model_id,
                    "revision": CE_MODEL_METADATA.revision,
                    "license": CE_MODEL_METADATA.license,
                    "access_status": "open",
                    "usage_restriction": "production",
                    "approval_state": "approved",
                    "production_gate": {
                        "colab_validation_passed": True,
                        "license_cleared": True,
                        "access_granted": True,
                        "quota_cleared": True,
                        "runtime_capacity_verified": True,
                    },
                },
                "formospeech_whisper_v3": {
                    "model_id": FORMO_MODEL_METADATA.model_id,
                    "revision": FORMO_MODEL_METADATA.revision,
                    "license": FORMO_MODEL_METADATA.license,
                    "access_status": "open",
                    "usage_restriction": "production",
                    "approval_state": "approved",
                    "production_gate": {
                        "colab_validation_passed": True,
                        "license_cleared": True,
                        "access_granted": True,
                        "quota_cleared": True,
                        "runtime_capacity_verified": True,
                    },
                },
            },
            "formo_prompt_id_allowlist": list(FORMO_ALLOWLIST),
            "concurrency": {"spill_wait_ms": 50},
        })

    def test_json_parses_into_valid_remote_only_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """合法 JSON 可建立可檢查的 provider registry 與路由表。"""
        monkeypatch.setenv(ENV_CONFIG_JSON, self._remote_only_json())
        from src.shared.asr.composition import load_config
        config = load_config()

        assert set(config.routes) == {"hak", "zh-TW"}
        assert config.routes["zh-TW"].provider_identifier == "ce_remote"
        assert config.routes["zh-TW"].fallback_chain == ("formo_remote",)

        # 所有 provider 都是 mock 或 remote_model
        for pc in config.providers.values():
            assert pc.kind in (ProviderKind.MOCK, ProviderKind.REMOTE_MODEL)

    def test_json_builds_remote_provider_instances(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """合法設定能建立 SageMakerAsrProvider 實例。"""
        monkeypatch.setenv(ENV_CONFIG_JSON, self._remote_only_json())
        from src.shared.asr.composition import load_config
        from src.shared.asr.remote_endpoints import SageMakerAsrProvider
        config = load_config()
        registry = build_provider_registry(config)

        assert "ce_remote" in registry
        assert "formo_remote" in registry
        assert isinstance(registry["ce_remote"], SageMakerAsrProvider)
        assert isinstance(registry["formo_remote"], SageMakerAsrProvider)

    def test_json_no_local_model_providers_built(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """remote-only JSON 不會建立 local model provider（已不存在）。"""
        monkeypatch.setenv(ENV_CONFIG_JSON, self._remote_only_json())
        from src.shared.asr.composition import load_config
        from src.shared.asr.remote_endpoints import SageMakerAsrProvider
        config = load_config()
        registry = build_provider_registry(config)

        # 所有非 mock 的 provider 都是 SageMakerAsrProvider
        for pid, instance in registry.items():
            if pid != "hak_mock":
                assert isinstance(instance, SageMakerAsrProvider), (
                    f"{pid} should be SageMakerAsrProvider, got {type(instance)}"
                )
