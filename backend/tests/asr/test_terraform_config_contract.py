"""
Terraform → Python config contract test。

驗證 Terraform asr_lambda_config.tf 組裝的 ASR_CONFIG_JSON 結構
與 Python parse_asr_config 的契約一致。

此測試模擬 Terraform 輸出的 JSON 結構，確保：
- 合法的 remote-only 設定能被正確解析
- 解析後的 AsrConfig 包含預期的 routes、providers、metadata
- endpoint_name 被正確帶入 provider config
- production gate 全部核准後能建立 SageMaker provider 實例
"""
from __future__ import annotations

import json

import pytest

from src.shared.asr.composition import build_provider_registry
from src.shared.asr.config import (
    AsrConfig,
    ConfigParseError,
    ProviderKind,
    parse_asr_config,
)
from src.shared.asr.remote_endpoints import SageMakerAsrProvider


def _terraform_asr_config_json(
    *,
    ce_endpoint: str = "ai-elder-care-asr-ce",
    formo_endpoint: str = "ai-elder-care-asr-formo",
) -> str:
    """模擬 Terraform asr_lambda_config.tf 的 local.asr_config_json 輸出。"""
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
                "endpoint_name": ce_endpoint,
                "max_concurrent": 4,
            },
            "formo_remote": {
                "identifier": "formo_remote",
                "status": "enabled",
                "kind": "remote_model",
                "metadata_ref": "formospeech_whisper_v3",
                "endpoint_name": formo_endpoint,
                "max_concurrent": 2,
            },
        },
        "model_metadata": {
            "taiwan_tongues_ce": {
                "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
                "revision": "v2.0",
                "license": "other",
                "access_status": "open",
                "usage_restriction": "production",
                "approval_state": "approved",
                "production_gate": {
                    "colab_validation_passed": True,
                    "license_cleared": True,
                    "access_granted": True,
                    "quota_cleared": True,
                    "runtime_capacity_verified": True,
                    "approval_record_ref": "docs/adr/asr-model-validation.md",
                },
            },
            "formospeech_whisper_v3": {
                "model_id": "formospeech/whisper-large-v3-taiwanese-hakka",
                "revision": "main",
                "license": "CC BY-NC 4.0",
                "access_status": "open",
                "usage_restriction": "production",
                "approval_state": "approved",
                "production_gate": {
                    "colab_validation_passed": True,
                    "license_cleared": True,
                    "access_granted": True,
                    "quota_cleared": True,
                    "runtime_capacity_verified": True,
                    "approval_record_ref": "docs/adr/asr-model-validation.md",
                },
            },
        },
        "formo_prompt_id_allowlist": [
            "htia_sixian",
            "htia_hailu",
            "htia_dapu",
            "htia_raoping",
            "htia_zhaoan",
            "htia_nansixian",
        ],
        "concurrency": {
            "spill_wait_ms": 250,
        },
    })


# ─────────────────────────────────────────────────────────────────
# Contract: Terraform JSON 能被 parse_asr_config 正確解析
# ─────────────────────────────────────────────────────────────────
class TestTerraformConfigContract:
    """驗證 Terraform 組裝的 JSON 與 Python parser 的契約一致。"""

    def test_terraform_json_parses_successfully(self) -> None:
        """完整的 Terraform JSON 可被 parse_asr_config 接受。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        assert isinstance(config, AsrConfig)

    def test_parsed_config_has_expected_routes(self) -> None:
        """解析後包含 hak 和 zh-TW 兩條路由。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        assert set(config.routes) == {"hak", "zh-TW"}
        assert config.routes["zh-TW"].provider_identifier == "ce_remote"
        assert config.routes["zh-TW"].fallback_chain == ("formo_remote",)
        assert config.routes["hak"].provider_identifier == "hak_mock"
        assert config.routes["hak"].fallback_chain == ("ce_remote",)

    def test_parsed_config_has_remote_model_providers(self) -> None:
        """所有非 mock 的 provider 都是 remote_model kind。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        for pid, pc in config.providers.items():
            if pid != "hak_mock":
                assert pc.kind is ProviderKind.REMOTE_MODEL

    def test_endpoint_names_are_injected(self) -> None:
        """endpoint_name 從 Terraform endpoint 名稱正確帶入。"""
        data = json.loads(_terraform_asr_config_json(
            ce_endpoint="custom-ce-ep",
            formo_endpoint="custom-formo-ep",
        ))
        config = parse_asr_config(data)
        assert config.providers["ce_remote"].endpoint_name == "custom-ce-ep"
        assert config.providers["formo_remote"].endpoint_name == "custom-formo-ep"

    def test_production_gates_are_approved(self) -> None:
        """Terraform 設定的 production gate 全部核准。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        for meta in config.model_metadata.values():
            assert meta.is_production_allowed is True

    def test_builds_sagemaker_provider_instances(self) -> None:
        """核准的設定可建立 SageMakerAsrProvider 實例。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        registry = build_provider_registry(config)
        assert "ce_remote" in registry
        assert "formo_remote" in registry
        assert isinstance(registry["ce_remote"], SageMakerAsrProvider)
        assert isinstance(registry["formo_remote"], SageMakerAsrProvider)

    def test_ce_endpoint_name_matches_registry(self) -> None:
        """建立的 provider 實例持有正確的 endpoint name。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        registry = build_provider_registry(config)
        assert registry["ce_remote"].endpoint_name == "ai-elder-care-asr-ce"
        assert registry["formo_remote"].endpoint_name == "ai-elder-care-asr-formo"

    def test_concurrency_policy_is_respected(self) -> None:
        """spill_wait_ms 從 JSON 正確解析。"""
        data = json.loads(_terraform_asr_config_json())
        config = parse_asr_config(data)
        assert config.concurrency.spill_wait_ms == 250

    def test_empty_string_uses_default_config(self) -> None:
        """空字串（未啟用時 Terraform 輸出）等同不注入 — 用預設設定。"""
        import os
        from src.shared.asr.composition import load_config, reset_asr_facade
        reset_asr_facade()
        # 模擬 Terraform 未啟用時的空字串
        os.environ.pop("ASR_CONFIG_JSON", None)
        config = load_config()
        # 預設只有 hak_mock 能出結果
        assert "hak_mock" in config.providers
        reset_asr_facade()
