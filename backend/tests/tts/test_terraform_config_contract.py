"""Terraform → Python TTS_CONFIG_JSON 組合與路由契約測試。"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from src.shared.tts.config import ProviderKind, TtsConfig, parse_tts_config

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
VOXHAKKA_DIALECTS = frozenset(HAKKA_DIALECTS[:-1])
ENABLE_COMBINATIONS = tuple(itertools.product((False, True), repeat=3))
MODEL_APPROVALS = {
    "omnivoice": True,
    "voxhakka": False,
    "breezyvoice": True,
}


def _production_gate(approved: bool) -> dict[str, object]:
    """模擬 Terraform 將單一模型 approval 同步至六項 gate。"""
    return {
        "staging_validation_passed": approved,
        "license_cleared": approved,
        "access_granted": approved,
        "quota_cleared": approved,
        "runtime_capacity_verified": approved,
        "latency_slo_verified": approved,
        "approval_record_ref": None,
    }


def _metadata(
    *, model_id: str, license_name: str, approved: bool
) -> dict[str, object]:
    """建立與 Terraform 相同的遠端模型 metadata。"""
    return {
        "model_id": model_id,
        "revision": "main",
        "license": license_name,
        "approved_for_production": approved,
        "production_gate": _production_gate(approved),
    }


def _terraform_tts_config(
    *,
    omnivoice_enabled: bool,
    voxhakka_enabled: bool,
    breezyvoice_enabled: bool,
) -> TtsConfig:
    """模擬三個 Terraform endpoint enable 開關組裝的 TTS_CONFIG_JSON。"""
    routes: dict[str, object] = {
        "zh-TW": {
            "route": "zh_tw_traditional",
            "enabled": True,
            "provider_identifier": (
                "breezyvoice_remote"
                if breezyvoice_enabled
                else "polly_zhiyu_neural"
            ),
            "fallback_chain": (
                ["polly_zhiyu_neural", "polly_zhiyu_standard"]
                if breezyvoice_enabled
                else ["polly_zhiyu_standard"]
            ),
        }
    }
    providers: dict[str, object] = {
        "production_disabled": {
            "kind": "mock",
            "status": "disabled",
            "languages": ["zh-TW", "hak"],
            "dialects": [],
        },
        "polly_zhiyu_neural": {
            "kind": "aws_managed",
            "status": "enabled",
            "languages": ["zh-TW"],
            "dialects": [],
            "voice_id": "Zhiyu",
            "engine": "neural",
        },
        "polly_zhiyu_standard": {
            "kind": "aws_managed",
            "status": "enabled",
            "languages": ["zh-TW"],
            "dialects": [],
            "voice_id": "Zhiyu",
            "engine": "standard",
        },
    }
    model_metadata: dict[str, object] = {}

    if omnivoice_enabled:
        providers["omnivoice_remote"] = {
            "kind": "remote_model",
            "status": "enabled",
            "languages": ["hak"],
            "dialects": list(HAKKA_DIALECTS),
            "metadata_ref": "omnivoice",
            "endpoint_name": "ai-elder-care-tts-omnivoice",
        }
        model_metadata["omnivoice"] = _metadata(
            model_id="formospeech/omnivoice-hakka-community-1",
            license_name="CC BY-NC 4.0",
            approved=MODEL_APPROVALS["omnivoice"],
        )
    if voxhakka_enabled:
        providers["voxhakka_remote"] = {
            "kind": "remote_model",
            "status": "enabled",
            "languages": ["hak"],
            "dialects": sorted(VOXHAKKA_DIALECTS),
            "metadata_ref": "voxhakka",
            "endpoint_name": "ai-elder-care-tts-voxhakka",
            "speaker": "XF",
        }
        model_metadata["voxhakka"] = _metadata(
            model_id="formospeech/yourtts-htia-240704",
            license_name="CC BY-NC 4.0",
            approved=MODEL_APPROVALS["voxhakka"],
        )
    if breezyvoice_enabled:
        providers["breezyvoice_remote"] = {
            "kind": "remote_model",
            "status": "enabled",
            "languages": ["zh-TW"],
            "dialects": [],
            "metadata_ref": "breezyvoice",
            "endpoint_name": "ai-elder-care-tts-breezyvoice",
        }
        model_metadata["breezyvoice"] = _metadata(
            model_id="MediaTek-Research/BreezyVoice",
            license_name="Apache-2.0",
            approved=MODEL_APPROVALS["breezyvoice"],
        )

    for dialect in HAKKA_DIALECTS:
        vox_supports_dialect = dialect in VOXHAKKA_DIALECTS
        routes[f"hak:{dialect}"] = {
            "route": f"hak_{dialect.removeprefix('htia_')}",
            "enabled": omnivoice_enabled
            or (voxhakka_enabled and vox_supports_dialect),
            "provider_identifier": (
                "omnivoice_remote"
                if omnivoice_enabled
                else (
                    "voxhakka_remote"
                    if voxhakka_enabled and vox_supports_dialect
                    else "production_disabled"
                )
            ),
            "fallback_chain": (
                ["voxhakka_remote"]
                if omnivoice_enabled
                and voxhakka_enabled
                and vox_supports_dialect
                else []
            ),
        }

    return parse_tts_config(
        {
            "schema_version": 1,
            "max_text_chars": 3000,
            "max_audio_bytes": 10 * 1024 * 1024,
            "routes": routes,
            "providers": providers,
            "model_metadata": model_metadata,
        }
    )


@pytest.mark.parametrize(
    ("omnivoice_enabled", "voxhakka_enabled", "breezyvoice_enabled"),
    ENABLE_COMBINATIONS,
    ids=[
        f"omni-{int(omni)}-vox-{int(vox)}-breezy-{int(breezy)}"
        for omni, vox, breezy in ENABLE_COMBINATIONS
    ],
)
def test_all_remote_model_enable_combinations_are_independent(
    omnivoice_enabled: bool,
    voxhakka_enabled: bool,
    breezyvoice_enabled: bool,
) -> None:
    """三個 endpoint 的 2^3 組合都能獨立組裝並同時存在。"""
    config = _terraform_tts_config(
        omnivoice_enabled=omnivoice_enabled,
        voxhakka_enabled=voxhakka_enabled,
        breezyvoice_enabled=breezyvoice_enabled,
    )
    expected = {
        provider_id: metadata_ref
        for provider_id, metadata_ref, enabled in (
            ("omnivoice_remote", "omnivoice", omnivoice_enabled),
            ("voxhakka_remote", "voxhakka", voxhakka_enabled),
            ("breezyvoice_remote", "breezyvoice", breezyvoice_enabled),
        )
        if enabled
    }
    actual = {
        provider_id: provider.metadata_ref
        for provider_id, provider in config.providers.items()
        if provider.kind is ProviderKind.REMOTE_MODEL
    }

    assert actual == expected
    assert set(config.model_metadata) == set(expected.values())
    for metadata_ref in expected.values():
        approved = MODEL_APPROVALS[metadata_ref]
        metadata = config.model_metadata[metadata_ref]
        gate = metadata.production_gate
        assert metadata.approved_for_production is approved
        assert gate.staging_validation_passed is approved
        assert gate.license_cleared is approved
        assert gate.access_granted is approved
        assert gate.quota_cleared is approved
        assert gate.runtime_capacity_verified is approved
        assert gate.latency_slo_verified is approved


@pytest.mark.parametrize("breezyvoice_enabled", (False, True))
def test_chinese_route_keeps_breezy_then_polly_order(
    breezyvoice_enabled: bool,
) -> None:
    """Breezy 開啟時優先，兩個 Polly engine 始終維持 Neural→Standard。"""
    config = _terraform_tts_config(
        omnivoice_enabled=False,
        voxhakka_enabled=False,
        breezyvoice_enabled=breezyvoice_enabled,
    )

    expected = (
        "breezyvoice_remote",
        "polly_zhiyu_neural",
        "polly_zhiyu_standard",
    ) if breezyvoice_enabled else (
        "polly_zhiyu_neural",
        "polly_zhiyu_standard",
    )
    assert config.routes["zh-TW"].provider_order == expected


def test_hakka_routes_keep_omnivoice_then_voxhakka_without_nansixian_vox() -> None:
    """五腔是 Omni→Vox；南四縣只能走 Omni，不得送入 Vox。"""
    config = _terraform_tts_config(
        omnivoice_enabled=True,
        voxhakka_enabled=True,
        breezyvoice_enabled=False,
    )

    for dialect in VOXHAKKA_DIALECTS:
        assert config.routes[f"hak:{dialect}"].provider_order == (
            "omnivoice_remote",
            "voxhakka_remote",
        )
    assert config.routes["hak:htia_nansixian"].provider_order == (
        "omnivoice_remote",
    )
    assert "htia_nansixian" not in config.providers["voxhakka_remote"].dialects


def test_voxhakka_only_leaves_nansixian_disabled() -> None:
    """只開 VoxHakka 時，南四縣 route 維持 fail closed。"""
    config = _terraform_tts_config(
        omnivoice_enabled=False,
        voxhakka_enabled=True,
        breezyvoice_enabled=False,
    )

    route = config.routes["hak:htia_nansixian"]
    assert route.enabled is False
    assert route.provider_order == ("production_disabled",)


def test_terraform_source_expresses_the_locked_route_order() -> None:
    """靜態鎖定 Terraform，而非只驗證測試側的等價模擬。"""
    source = (TERRAFORM_DIR / "tts_lambda_config.tf").read_text(encoding="utf-8")

    assert re.search(
        r'provider_identifier\s*=\s*var\.tts_enable_breezyvoice_endpoint\s*\?\s*'
        r'"breezyvoice_remote"\s*:\s*"polly_zhiyu_neural"',
        source,
    )
    assert re.search(
        r'fallback_chain\s*=\s*var\.tts_enable_breezyvoice_endpoint\s*\?\s*'
        r'\[\s*"polly_zhiyu_neural",\s*"polly_zhiyu_standard",\s*\]\s*'
        r':\s*\["polly_zhiyu_standard"\]',
        source,
    )
    assert re.search(
        r'fallback_chain\s*=\s*\(\s*var\.tts_enable_omnivoice_endpoint\s*&&\s*'
        r'var\.tts_enable_voxhakka_endpoint\s*&&\s*'
        r'contains\(local\.tts_voxhakka_dialects, dialect\)\s*\)\s*\?\s*'
        r'\["voxhakka_remote"\]\s*:\s*\[\]',
        source,
    )
