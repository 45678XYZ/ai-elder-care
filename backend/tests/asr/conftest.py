# ASR-only 測試設定
# ─────────────────────────────────────────────────────────────────
# 必要環境與執行指令：
#   conda env create -f ../asr-lambda/environment.yml   # 首次建立
#   conda activate asr-model
#   python -m pip install -e ".[dev]"
#   python -m pytest tests/asr -q
# ─────────────────────────────────────────────────────────────────
"""
ASR-only 共用測試設定。

本模組提供：
1. Hypothesis profile（每項 property 至少 100 iterations）
2. 網路阻斷 fixture（防止測試中意外建立網路連線）

所有測試皆為必要測試，不使用 optional markers 或 skip conditions。
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
from hypothesis import HealthCheck, settings

# ─────────────────────────────────────────────────────────────────
# Hypothesis profile：每項 property test 至少 100 iterations
# ─────────────────────────────────────────────────────────────────
settings.register_profile(
    "asr",
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("asr")


# ─────────────────────────────────────────────────────────────────
# 網路阻斷 fixture — 防止任何意外的外部網路呼叫
# 滿足需求 8.13：不建立 AWS 網路呼叫、真實模型呼叫等
# ─────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """阻斷所有 socket 連線，確保測試不會產生任何網路呼叫。"""

    def _deny_socket(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        raise OSError(
            "Network access is disabled in ASR-only tests. "
            "If you see this error, the code under test is attempting "
            "an unexpected network connection."
        )

    monkeypatch.setattr(socket, "socket", _deny_socket)
