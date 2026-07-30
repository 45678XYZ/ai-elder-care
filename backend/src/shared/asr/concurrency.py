"""
ASR 併發控制 — bounded slot pool 與 thread-safe lazy model handle。

為什麼需要這一層：實體 ASR 模型（faster-whisper / transformers）的推論 session
不是可重入的，同一個 handle 被多執行緒同時呼叫會產生未定義行為；而 2B 參數模型
重複載入會直接耗盡記憶體。因此併發的正確性不能靠 provider 各自處理，必須由
共用的 slot pool 與單次載入的 handle 保證。

兩者都是 deadline-aware：等待取號與等待模型載入都有上限，絕不讓呼叫端的
deadline 被無界等待吃掉。

禁止依賴：handlers、HTTP、DB、AWS SDK、模型推論套件（本模組不 import 任何模型）。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Generic, Iterator, TypeVar

T = TypeVar("T")


class ModelLoadUnavailable(RuntimeError):
    """
    模型 handle 目前無法取得。

    兩種情形：載入失敗且仍在冷卻期內，或等待載入超過允許時間。
    呼叫端（provider）必須把它正規化為 provider_unavailable，不可外洩原始例外文字。
    """


# ─────────────────────────────────────────────────────────────────
# Slot pool — 限制單一 provider 的同時推論數
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SlotPoolStats:
    """slot pool 的瞬時狀態快照，供備援鏈判斷是否溢流到下一個 provider。"""

    provider_id: str
    capacity: int
    in_flight: int

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.in_flight)

    @property
    def is_saturated(self) -> bool:
        return self.available == 0


@dataclass(frozen=True)
class SlotLease:
    """
    取號結果。

    `acquired=False` 代表在允許的等待時間內沒有拿到 slot，呼叫端應視為飽和，
    而不是視為模型故障——這兩者在備援鏈裡的處理方式不同。
    """

    acquired: bool
    wait_seconds: float

    @property
    def wait_ms(self) -> int:
        return max(0, int(self.wait_seconds * 1000))


class ModelSlotPool:
    """
    單一 provider 的併發上限閘門。

    容量代表「這個 provider 可以同時跑幾個推論」，通常等於模型實體數或
    GPU 能同時容納的 batch 數。取號採 bounded wait：等不到就回報飽和，
    讓上層決定要溢流到備援還是回報忙碌，不做無界排隊。
    """

    __slots__ = ("_provider_id", "_capacity", "_semaphore", "_lock", "_in_flight", "_clock")

    def __init__(
        self,
        provider_id: str,
        capacity: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("ModelSlotPool.provider_id must be a non-blank string.")
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("ModelSlotPool.capacity must be an integer >= 1.")
        self._provider_id = provider_id
        self._capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._clock = clock

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capacity(self) -> int:
        return self._capacity

    def stats(self) -> SlotPoolStats:
        with self._lock:
            in_flight = self._in_flight
        return SlotPoolStats(
            provider_id=self._provider_id,
            capacity=self._capacity,
            in_flight=in_flight,
        )

    @contextmanager
    def lease(self, max_wait_seconds: float) -> Iterator[SlotLease]:
        """
        嘗試取得一個推論 slot。

        Args:
            max_wait_seconds: 等待上限。<= 0 表示不等待（立刻判定飽和）。

        Yields:
            SlotLease；`acquired=False` 時呼叫端不得執行推論。
        """
        started = self._clock()
        if max_wait_seconds <= 0:
            acquired = self._semaphore.acquire(blocking=False)
        else:
            acquired = self._semaphore.acquire(timeout=max_wait_seconds)

        waited = max(0.0, self._clock() - started)

        if acquired:
            with self._lock:
                self._in_flight += 1
        try:
            yield SlotLease(acquired=acquired, wait_seconds=waited)
        finally:
            if acquired:
                with self._lock:
                    self._in_flight -= 1
                self._semaphore.release()


# ─────────────────────────────────────────────────────────────────
# Lazy model handle — 每個 process 只載入一次
# ─────────────────────────────────────────────────────────────────
class LazyModelHandle(Generic[T]):
    """
    延遲、單次、thread-safe 的模型 handle。

    採 double-checked locking：已載入時走無鎖快路徑；未載入時只有第一個執行緒
    真正執行載入，其餘在鎖上等待，避免同一模型被載入多份而爆記憶體。

    載入失敗不會永久毒化 handle，但會進入冷卻期，避免每個請求都重試一次
    昂貴的下載或初始化。
    """

    __slots__ = (
        "_name",
        "_loader",
        "_lock",
        "_handle",
        "_failed_at",
        "_failure_count",
        "_cooldown_seconds",
        "_clock",
    )

    def __init__(
        self,
        name: str,
        loader: Callable[[], T],
        retry_cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("LazyModelHandle.name must be a non-blank string.")
        if retry_cooldown_seconds < 0:
            raise ValueError("retry_cooldown_seconds must be non-negative.")
        self._name = name
        self._loader = loader
        self._lock = threading.Lock()
        self._handle: T | None = None
        self._failed_at: float | None = None
        self._failure_count = 0
        self._cooldown_seconds = retry_cooldown_seconds
        self._clock = clock

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_loaded(self) -> bool:
        return self._handle is not None

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def get(self, max_wait_seconds: float | None = None) -> T:
        """
        取得模型 handle，必要時觸發載入。

        Args:
            max_wait_seconds: 等待其他執行緒完成載入的上限；None 表示無上限等待。

        Raises:
            ModelLoadUnavailable: 等待逾時、仍在失敗冷卻期，或載入本身失敗。
        """
        handle = self._handle
        if handle is not None:
            return handle

        if max_wait_seconds is None:
            got_lock = self._lock.acquire()
        elif max_wait_seconds <= 0:
            got_lock = self._lock.acquire(blocking=False)
        else:
            got_lock = self._lock.acquire(timeout=max_wait_seconds)

        if not got_lock:
            raise ModelLoadUnavailable(
                f"Timed out waiting for model handle {self._name!r} to become ready."
            )

        try:
            if self._handle is not None:
                return self._handle

            if self._failed_at is not None:
                elapsed = self._clock() - self._failed_at
                if elapsed < self._cooldown_seconds:
                    raise ModelLoadUnavailable(
                        f"Model handle {self._name!r} is in load-failure cooldown."
                    )

            try:
                loaded = self._loader()
            except Exception:
                self._failed_at = self._clock()
                self._failure_count += 1
                # from None：不讓底層 exception 文字沿著呼叫鏈外洩
                raise ModelLoadUnavailable(
                    f"Model handle {self._name!r} failed to load."
                ) from None

            if loaded is None:
                self._failed_at = self._clock()
                self._failure_count += 1
                raise ModelLoadUnavailable(
                    f"Model loader for {self._name!r} returned no handle."
                )

            self._handle = loaded
            self._failed_at = None
            return loaded
        finally:
            self._lock.release()

    def reset(self) -> None:
        """丟棄已載入的 handle 與失敗狀態。僅供測試與組裝重建使用。"""
        with self._lock:
            self._handle = None
            self._failed_at = None
            self._failure_count = 0
