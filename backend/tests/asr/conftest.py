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
3. 共用假件（fake transport spy、fake telemetry sink）

所有測試皆為必要測試，不使用 optional markers 或 skip conditions。
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
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


# ─────────────────────────────────────────────────────────────────
# 共用假件 — Fake Transport Spy
# ─────────────────────────────────────────────────────────────────
@dataclass
class FakeTransportCall:
    """紀錄一次對 fake transport 的呼叫。"""

    audio_bytes: bytes
    fields: dict[str, Any]


@dataclass
class FakeTransportSpy:
    """
    可注入的假 transport，記錄所有收到的呼叫而不執行任何外部操作。
    用於驗證 adapter contract 只將 Canonical Audio 與允許欄位傳入 transport。
    """

    calls: list[FakeTransportCall] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=lambda: {"text": ""})
    should_raise: Exception | None = None

    def invoke(self, audio_bytes: bytes, **fields: Any) -> dict[str, Any]:
        self.calls.append(FakeTransportCall(audio_bytes=audio_bytes, fields=fields))
        if self.should_raise is not None:
            raise self.should_raise
        return self.response


@pytest.fixture()
def fake_transport() -> FakeTransportSpy:
    """提供一個乾淨的 FakeTransportSpy 實例。"""
    return FakeTransportSpy()


# ─────────────────────────────────────────────────────────────────
# 共用假件 — Fake Telemetry Sink
# ─────────────────────────────────────────────────────────────────
@dataclass
class TelemetryRecord:
    """一筆遙測紀錄。"""

    event_type: str
    payload: dict[str, Any]


@dataclass
class FakeTelemetrySink:
    """
    假遙測接收器，收集所有發出的遙測事件。
    用於驗證 Safe Telemetry 的 allowlist 與終態單一性。
    """

    records: list[TelemetryRecord] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.records.append(TelemetryRecord(event_type=event_type, payload=payload))


@pytest.fixture()
def fake_telemetry() -> FakeTelemetrySink:
    """提供一個乾淨的 FakeTelemetrySink 實例。"""
    return FakeTelemetrySink()
