"""
併發基礎設施測試 — ModelSlotPool 與 LazyModelHandle。

驗證重點：容量上限不被突破、bounded wait 會如期放棄、模型只載入一次、
載入失敗進入冷卻期。不載入任何真實模型、不連網。
"""
from __future__ import annotations

import threading
import time

import pytest

from src.shared.asr.concurrency import (
    LazyModelHandle,
    ModelLoadUnavailable,
    ModelSlotPool,
)


# ─────────────────────────────────────────────────────────────────
# ModelSlotPool
# ─────────────────────────────────────────────────────────────────
def test_slot_pool_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        ModelSlotPool("p", 0)
    with pytest.raises(ValueError):
        ModelSlotPool("", 1)


def test_slot_pool_grants_within_capacity() -> None:
    pool = ModelSlotPool("p", 2)
    with pool.lease(0.0) as first:
        assert first.acquired
        assert pool.stats().in_flight == 1
        with pool.lease(0.0) as second:
            assert second.acquired
            assert pool.stats().in_flight == 2
            assert pool.stats().is_saturated
    assert pool.stats().in_flight == 0


def test_slot_pool_reports_saturation_without_waiting() -> None:
    """容量用盡且 max_wait=0 時必須立刻回報未取得，而不是阻塞。"""
    pool = ModelSlotPool("p", 1)
    with pool.lease(0.0) as held:
        assert held.acquired
        started = time.monotonic()
        with pool.lease(0.0) as denied:
            assert not denied.acquired
        assert time.monotonic() - started < 0.2


def test_slot_pool_bounded_wait_gives_up() -> None:
    """等待有上限：持有者不釋放時，等待方必須在上限附近放棄。"""
    pool = ModelSlotPool("p", 1)
    with pool.lease(0.0) as held:
        assert held.acquired
        started = time.monotonic()
        with pool.lease(0.15) as denied:
            assert not denied.acquired
            assert denied.wait_ms >= 100
        assert time.monotonic() - started < 1.0


def test_slot_pool_releases_slot_when_body_raises() -> None:
    """推論爆炸也必須歸還 slot，否則容量會被永久吃掉。"""
    pool = ModelSlotPool("p", 1)
    with pytest.raises(RuntimeError):
        with pool.lease(0.0) as lease:
            assert lease.acquired
            raise RuntimeError("boom")
    assert pool.stats().in_flight == 0
    with pool.lease(0.0) as again:
        assert again.acquired


def test_slot_pool_never_exceeds_capacity_under_concurrency() -> None:
    """核心併發不變量：同時進行的數量永遠不超過容量。"""
    capacity = 3
    pool = ModelSlotPool("p", capacity)
    observed_peak = 0
    peak_lock = threading.Lock()
    granted = 0
    granted_lock = threading.Lock()

    def worker() -> None:
        nonlocal observed_peak, granted
        with pool.lease(5.0) as lease:
            if not lease.acquired:
                return
            with granted_lock:
                granted += 1
            with peak_lock:
                observed_peak = max(observed_peak, pool.stats().in_flight)
            time.sleep(0.02)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert granted == 12
    assert observed_peak <= capacity
    assert pool.stats().in_flight == 0


# ─────────────────────────────────────────────────────────────────
# LazyModelHandle
# ─────────────────────────────────────────────────────────────────
def test_lazy_handle_loads_lazily_and_only_once() -> None:
    calls: list[int] = []

    def loader() -> str:
        calls.append(1)
        return "handle"

    handle = LazyModelHandle("m", loader)
    assert not handle.is_loaded
    assert calls == []

    assert handle.get() == "handle"
    assert handle.get() == "handle"
    assert handle.is_loaded
    assert len(calls) == 1


def test_lazy_handle_loads_once_under_concurrency() -> None:
    """重複載入 2B 參數模型會爆記憶體，所以併發只能觸發一次 loader。"""
    calls: list[int] = []
    calls_lock = threading.Lock()

    def slow_loader() -> str:
        with calls_lock:
            calls.append(1)
        time.sleep(0.05)
        return "handle"

    handle = LazyModelHandle("m", slow_loader)
    results: list[str] = []
    results_lock = threading.Lock()

    def worker() -> None:
        value = handle.get()
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert results == ["handle"] * 8


def test_lazy_handle_load_failure_raises_typed_error_without_leaking_text() -> None:
    def failing_loader() -> str:
        raise RuntimeError("token=super-secret /home/user/model.bin")

    handle = LazyModelHandle("m", failing_loader, retry_cooldown_seconds=10.0)

    with pytest.raises(ModelLoadUnavailable) as first:
        handle.get()
    assert "super-secret" not in str(first.value)
    assert handle.failure_count == 1

    # 冷卻期內不重試 loader
    with pytest.raises(ModelLoadUnavailable):
        handle.get()
    assert handle.failure_count == 1


def test_lazy_handle_retries_after_cooldown_elapses() -> None:
    attempts: list[int] = []
    fake_now = [0.0]

    def flaky_loader() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "handle"

    handle = LazyModelHandle(
        "m",
        flaky_loader,
        retry_cooldown_seconds=5.0,
        clock=lambda: fake_now[0],
    )

    with pytest.raises(ModelLoadUnavailable):
        handle.get()

    fake_now[0] = 6.0
    assert handle.get() == "handle"
    assert len(attempts) == 2


def test_lazy_handle_rejects_loader_returning_none() -> None:
    handle = LazyModelHandle("m", lambda: None)
    with pytest.raises(ModelLoadUnavailable):
        handle.get()


def test_lazy_handle_bounded_wait_times_out() -> None:
    """等待別人完成載入也有上限，避免慢速載入吃掉呼叫端 deadline。"""
    release = threading.Event()

    def blocking_loader() -> str:
        release.wait(timeout=5.0)
        return "handle"

    handle = LazyModelHandle("m", blocking_loader)
    holder = threading.Thread(target=handle.get)
    holder.start()
    time.sleep(0.05)

    with pytest.raises(ModelLoadUnavailable):
        handle.get(max_wait_seconds=0.1)

    release.set()
    holder.join()
    assert handle.get() == "handle"
