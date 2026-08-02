"""
Terraform → Python ASR 設定與語音基礎設施契約測試。

驗證 `terraform/asr_lambda_config.tf` 組裝的 JSON 可由 Python parser 接受，並鎖定
Transcribe／CE／Formo 路由、staging gate、固定 GPU 配置與最小 IAM 權限。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.shared.asr.composition import build_provider_registry
from src.shared.asr.config import AsrConfig, ProviderKind, parse_asr_config
from src.shared.asr.providers import AMAZON_TRANSCRIBE_PROVIDER_ID

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRAFORM_DIR = REPO_ROOT / "terraform"
HAKKA_DIALECTS = (
    "htia_sixian",
    "htia_hailu",
    "htia_dapu",
    "htia_raoping",
    "htia_zhaoan",
    "htia_nansixian",
)


def _formo_endpoint_names() -> dict[str, str]:
    """回傳 Terraform 固定產生的六腔 endpoint 名稱。"""
    return {
        dialect: f"ai-elder-care-asr-formo-{dialect.removeprefix('htia_')}"
        for dialect in HAKKA_DIALECTS
    }


def _model_metadata() -> dict[str, object]:
    """模擬尚未取得 staging/runtime 核准時的 Terraform metadata。"""
    incomplete_gate = {
        "staging_validation_passed": False,
        "license_cleared": False,
        "access_granted": False,
        "quota_cleared": False,
        "runtime_capacity_verified": False,
        "approval_record_ref": None,
    }
    formo_gate = dict(incomplete_gate)
    formo_gate["access_granted"] = True
    return {
        "taiwan_tongues_ce": {
            "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
            "revision": "v2.0",
            "license": "other",
            "access_status": "open",
            "usage_restriction": "staging_validation_only",
            "approval_state": "not_approved",
            "production_gate": dict(incomplete_gate),
        },
        "formospeech_whisper_v3": {
            "model_id": "formospeech/whisper-large-v3-taiwanese-hakka",
            "revision": "main",
            "license": "CC BY-NC 4.0",
            "access_status": "gated",
            "usage_restriction": "staging_validation_only",
            "approval_state": "not_approved",
            "production_gate": formo_gate,
        },
    }


def _terraform_asr_config_json(*, endpoints_enabled: bool = True) -> str:
    """模擬 `local.asr_config_json` 在 endpoint 開關兩種狀態下的輸出。"""
    routes: dict[str, object] = {
        "zh-TW": {
            "route": "zh_tw_primary",
            "provider_identifier": AMAZON_TRANSCRIBE_PROVIDER_ID,
            "enabled": True,
            "fallback_chain": ["ce_remote"] if endpoints_enabled else [],
        }
    }
    providers: dict[str, object] = {
        AMAZON_TRANSCRIBE_PROVIDER_ID: {
            "identifier": AMAZON_TRANSCRIBE_PROVIDER_ID,
            "status": "enabled",
            "kind": "aws_managed",
        }
    }

    if endpoints_enabled:
        providers["ce_remote"] = {
            "identifier": "ce_remote",
            "status": "enabled",
            "kind": "remote_model",
            "metadata_ref": "taiwan_tongues_ce",
            "endpoint_name": "ai-elder-care-asr-ce",
        }
        for dialect, endpoint_name in _formo_endpoint_names().items():
            provider_id = f"formo_remote_{dialect}"
            routes[f"hak:{dialect}"] = {
                "route": f"hak_{dialect.removeprefix('htia_')}_primary",
                "provider_identifier": provider_id,
                "enabled": True,
                "fallback_chain": ["ce_remote"],
            }
            providers[provider_id] = {
                "identifier": provider_id,
                "status": "enabled",
                "kind": "remote_model",
                "metadata_ref": "formospeech_whisper_v3",
                "endpoint_name": endpoint_name,
            }
        metadata = _model_metadata()
    else:
        providers["production_disabled"] = {
            "identifier": "production_disabled",
            "status": "disabled",
            "kind": "mock",
        }
        for dialect in HAKKA_DIALECTS:
            routes[f"hak:{dialect}"] = {
                "route": f"hak_{dialect.removeprefix('htia_')}_disabled",
                "provider_identifier": "production_disabled",
                "enabled": False,
                "fallback_chain": [],
            }
        metadata = {}

    return json.dumps(
        {
            "routes": routes,
            "providers": providers,
            "model_metadata": metadata,
        }
    )


def _terraform_source(filename: str) -> str:
    """讀取 Terraform 原始碼供靜態基礎設施契約檢查。"""
    return (TERRAFORM_DIR / filename).read_text(encoding="utf-8")


class TestTerraformConfigContract:
    """驗證 Terraform JSON 與 Python ASR parser/composition 的契約。"""

    def test_enabled_terraform_json_parses_successfully(self) -> None:
        """完整的 endpoint-enabled JSON 可被 parser 接受。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))
        assert isinstance(config, AsrConfig)

    def test_routes_use_transcribe_and_formo_with_ce_fallback(self) -> None:
        """中文以 Transcribe 為主，六腔以各自 Formo 為主，CE 是共同備援。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))

        assert set(config.routes) == {
            "zh-TW",
            *(f"hak:{dialect}" for dialect in HAKKA_DIALECTS),
        }
        assert (
            config.routes["zh-TW"].provider_identifier
            == AMAZON_TRANSCRIBE_PROVIDER_ID
        )
        assert config.routes["zh-TW"].fallback_chain == ("ce_remote",)
        for dialect in HAKKA_DIALECTS:
            route = config.routes[f"hak:{dialect}"]
            assert route.provider_identifier == f"formo_remote_{dialect}"
            assert route.fallback_chain == ("ce_remote",)

    def test_provider_kinds_match_managed_and_remote_boundaries(self) -> None:
        """Transcribe 不綁模型 gate；CE 與六個 Formo 都是遠端模型。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))

        transcribe = config.providers[AMAZON_TRANSCRIBE_PROVIDER_ID]
        assert transcribe.kind is ProviderKind.AWS_MANAGED
        assert transcribe.metadata_ref is None
        assert transcribe.endpoint_name is None
        assert config.providers["ce_remote"].kind is ProviderKind.REMOTE_MODEL
        for dialect in HAKKA_DIALECTS:
            assert (
                config.providers[f"formo_remote_{dialect}"].kind
                is ProviderKind.REMOTE_MODEL
            )

    def test_sagemaker_endpoint_names_are_injected(self) -> None:
        """CE 與六腔 endpoint 名稱正確帶入 provider config。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))

        assert config.providers["ce_remote"].endpoint_name == "ai-elder-care-asr-ce"
        for dialect, endpoint_name in _formo_endpoint_names().items():
            assert (
                config.providers[f"formo_remote_{dialect}"].endpoint_name
                == endpoint_name
            )

    def test_sagemaker_production_gates_are_closed(self) -> None:
        """尚無 staging/runtime 證據時，CE 與 Formo production gate 關閉。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))

        for metadata in config.model_metadata.values():
            assert metadata.is_production_allowed is False
            assert metadata.production_gate.is_approved is False
            assert metadata.production_gate.approval_record_ref is None

    def test_registry_only_builds_managed_provider_before_model_approval(
        self,
    ) -> None:
        """SageMaker 模型未核准時只建立受控的 Transcribe provider。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))

        registry = build_provider_registry(config)
        assert set(registry) == {AMAZON_TRANSCRIBE_PROVIDER_ID}

    def test_formo_metadata_is_gated_and_staging_only(self) -> None:
        """Formo 存取狀態為 gated，核准用途停留在 staging。"""
        config = parse_asr_config(json.loads(_terraform_asr_config_json()))
        metadata = config.model_metadata["formospeech_whisper_v3"]

        assert metadata.access_status.value == "gated"
        assert metadata.usage_restriction.value == "staging_validation_only"
        assert metadata.approval_state.value == "not_approved"
        assert metadata.production_gate.access_granted is True
        assert (
            config.model_metadata["taiwan_tongues_ce"]
            .production_gate.access_granted
            is False
        )

    def test_disabled_endpoints_keep_transcribe_and_disable_hakka(self) -> None:
        """關閉 SageMaker 時中文仍可用 Transcribe，六腔則 fail closed。"""
        config = parse_asr_config(
            json.loads(_terraform_asr_config_json(endpoints_enabled=False))
        )

        assert config.routes["zh-TW"].enabled is True
        assert config.routes["zh-TW"].fallback_chain == ()
        assert set(config.providers) == {
            AMAZON_TRANSCRIBE_PROVIDER_ID,
            "production_disabled",
        }
        assert config.model_metadata == {}
        for dialect in HAKKA_DIALECTS:
            assert config.routes[f"hak:{dialect}"].enabled is False

    def test_removed_local_concurrency_fields_are_absent(self) -> None:
        """Lambda 設定不包含程序內模型槽或等待佇列。"""
        data = json.loads(_terraform_asr_config_json())
        assert "concurrency" not in data
        assert all(
            "max_concurrent" not in provider
            for provider in data["providers"].values()
        )

    def test_empty_environment_uses_local_default_config(self, monkeypatch) -> None:
        """未注入環境變數時仍使用明確的本機測試設定。"""
        from src.shared.asr.composition import load_config, reset_asr_facade

        monkeypatch.delenv("ASR_CONFIG_JSON", raising=False)
        reset_asr_facade()
        config = load_config()
        assert "hak_mock" in config.providers
        reset_asr_facade()


class TestTerraformSpeechInfrastructureContract:
    """鎖定競賽環境的 Region、IAM、GPU 與固定容量設定。"""

    def test_region_is_limited_to_competition_regions(self) -> None:
        """Terraform 變數只接受兩個競賽指定 AWS Region。"""
        source = _terraform_source("variables.tf")
        assert 'contains(["us-east-1", "us-west-2"], var.aws_region)' in source

    def test_transcribe_streaming_permission_is_unconditional(self) -> None:
        """Chat Lambda 基礎 policy 無條件包含 Transcribe Streaming 權限。"""
        source = _terraform_source("lambda.tf")
        assert '"transcribe:StartStreamTranscription"' in source

    def test_asr_models_use_fixed_instances_and_official_quotas(self) -> None:
        """七個 ASR endpoint 固定一台，機型分配不超過官方配額。"""
        source = _terraform_source("asr_models.tf")
        expected = {
            "htia_sixian": "ml.g5.2xlarge",
            "htia_hailu": "ml.g5.2xlarge",
            "htia_dapu": "ml.g5.xlarge",
            "htia_raoping": "ml.g5.xlarge",
            "htia_zhaoan": "ml.g4dn.2xlarge",
            "htia_nansixian": "ml.g4dn.2xlarge",
        }
        for dialect, instance_type in expected.items():
            assert re.search(
                rf"{dialect}\s*=\s*\"{re.escape(instance_type)}\"", source
            )
        assert re.search(r'asr_ce_instance_type\s*=\s*"ml\.g5\.4xlarge"', source)
        assert source.count("initial_instance_count = 1") == 2
        assert 'FORMO_GENERATION_LANGUAGE = "Chinese"' in source
        assert 'check "asr_endpoint_instance_quotas"' in source
        assert "aws_appautoscaling_target" not in source
        assert "aws_appautoscaling_policy" not in source

    def test_tts_models_use_per_model_fixed_instances_and_quotas(self) -> None:
        """三個 TTS 模型使用逐模型固定機型且不建立 autoscaling。"""
        source = _terraform_source("tts_models.tf")
        assert source.count('instance_type = "ml.g4dn.xlarge"') == 2
        # BreezyVoice 用 A10G：T4 上每段文字要 20-25 秒，多段回覆動輒破分鐘。
        assert source.count('instance_type = "ml.g5.4xlarge"') == 1
        assert source.count("initial_instance_count = 1") == 1
        assert 'check "tts_endpoint_instance_quotas"' in source
        assert "aws_appautoscaling_target" not in source
        assert "aws_appautoscaling_policy" not in source

    def test_obsolete_shared_capacity_variables_are_removed(self) -> None:
        """固定容量後不再暴露 shared min/max 或 autoscaling 變數。"""
        source = _terraform_source("variables.tf")
        obsolete = {
            "asr_ce_instance_type",
            "asr_ce_min_instances",
            "asr_ce_max_instances",
            "asr_formo_instance_type",
            "asr_formo_min_instances",
            "asr_formo_max_instances",
            "asr_target_invocations_per_instance",
            "tts_instance_type",
            "tts_min_instances",
            "tts_max_instances",
        }
        assert all(f'variable "{name}"' not in source for name in obsolete)
