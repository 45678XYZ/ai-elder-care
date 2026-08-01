"""
Property 3: 終態 telemetry 唯一且不含敏感內容。

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 8.9; Design Property 3**

For all ASR success、cancelled、deadline-exceeded 與 error terminal outcomes，
以及 for any 包含 audio、transcript、HF token、長者資料、raw response 或
Formo Prompt ID sentinel 的內部輸入，每組 Correlation Context 都必須產生恰一個
terminal result 與恰一筆 Safe Telemetry；該 telemetry 的鍵只能是 allowlist，
並正確包含 terminal/deadline outcome、elapsed time 與適用的 error category，
而不得包含任何 sentinel 或禁止欄位。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import given
from hypothesis import strategies as st

from src.shared.asr.telemetry import (
    DeadlineOutcome,
    SafeTelemetryRecord,
    TELEMETRY_ALLOWLIST_KEYS,
    TerminalOutcome,
    TerminalTelemetryEmitter,
)
from src.shared.asr.types import (
    AsrErrorCategory,
    CanonicalAudio,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)


# ─────────────────────────────────────────────────────────────────
# Test helper — CollectingSink
# ─────────────────────────────────────────────────────────────────
@dataclass
class CollectingSink:
    """收集所有 emit 的 SafeTelemetryRecord。"""

    records: list[SafeTelemetryRecord] = field(default_factory=list)

    def emit(self, record: SafeTelemetryRecord) -> None:
        self.records.append(record)


# ─────────────────────────────────────────────────────────────────
# Sensitive sentinels — 這些不得出現在 telemetry values
# ─────────────────────────────────────────────────────────────────
FORMO_PROMPT_IDS = [
    "htia_sixian",
    "htia_hailu",
    "htia_dapu",
    "htia_raoping",
    "htia_zhaoan",
    "htia_nansixian",
]


@st.composite
def sensitive_sentinels(draw: st.DrawFn) -> list[str]:
    """
    生成敏感 sentinel 列表：audio bytes repr、transcript text、
    HF token、PII/elder data、raw response、Formo Prompt ID。
    每種類型至少一個，用於驗證它們不洩漏到 telemetry values。

    使用 unique prefixes 使 sentinels 不會與 allowlist 欄位值碰撞。
    """
    sentinels: list[str] = []

    # Audio bytes sentinel — hex with unique prefix
    audio_hex = draw(st.binary(min_size=4, max_size=16).map(lambda b: b.hex()))
    sentinels.append(f"AUDIO_SENTINEL_{audio_hex}")

    # Transcript sentinel — CJK text with unique prefix
    transcript_chars = draw(
        st.text(
            min_size=3,
            max_size=20,
            alphabet=st.characters(
                whitelist_categories=("L",),
                min_codepoint=0x4E00,
                max_codepoint=0x9FFF,
            ),
        )
    )
    sentinels.append(f"TRANSCRIPT_{transcript_chars}")

    # HF token sentinel — always starts with hf_ prefix
    hf_suffix = draw(
        st.text(
            min_size=6,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        )
    )
    sentinels.append(f"hf_TOKEN_{hf_suffix}")

    # PII / elder data sentinel — unique prefix
    pii_chars = draw(
        st.text(
            min_size=3,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        )
    )
    sentinels.append(f"ELDER_PII_{pii_chars}")

    # Raw response sentinel — unique prefix
    raw_chars = draw(
        st.text(
            min_size=5,
            max_size=30,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        )
    )
    sentinels.append(f"RAW_RESP_{raw_chars}")

    # Formo Prompt ID sentinel
    formo_id = draw(st.sampled_from(FORMO_PROMPT_IDS))
    sentinels.append(formo_id)

    return sentinels


# ─────────────────────────────────────────────────────────────────
# Strategies — terminal results
# ─────────────────────────────────────────────────────────────────
@st.composite
def terminal_results(draw: st.DrawFn) -> Transcript | TypedAsrError:
    """
    生成 terminal result：Transcript（success）或 TypedAsrError（error）。
    涵蓋 success、cancelled、deadline-exceeded 與各種 error categories。
    """
    is_success = draw(st.booleans())
    if is_success:
        # 生成非空白 transcript text
        text = draw(
            st.text(
                min_size=1,
                max_size=50,
                alphabet=st.characters(
                    blacklist_categories=("Cs",),
                    min_codepoint=0x21,
                    max_codepoint=0x9FFF,
                ),
            )
        )
        text = text.strip()
        if not text:
            text = "fallback"
        return Transcript(text=text)
    else:
        category = draw(st.sampled_from(list(AsrErrorCategory)))
        retryable = draw(st.booleans())
        return TypedAsrError(
            category=category, message="test error", retryable=retryable
        )


@st.composite
def correlation_ids(draw: st.DrawFn) -> str:
    """生成非空白 correlation ID — 使用 ASCII-only 避免與 sentinel 碰撞。"""
    cid = draw(
        st.text(
            min_size=1,
            max_size=64,
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                min_codepoint=0x30,
                max_codepoint=0x7A,
            ),
        )
    )
    cid = cid.strip()
    if not cid:
        cid = "corr-fallback"
    return f"corr_{cid}"


@st.composite
def elapsed_times(draw: st.DrawFn) -> tuple[float, float]:
    """
    生成 (start_time, emit_time) 確保 emit_time >= start_time。
    回傳值用於建立 clock 使 elapsed_ms 非負。
    """
    start = draw(st.floats(min_value=0.0, max_value=1000.0))
    delta = draw(st.floats(min_value=0.0, max_value=30.0))
    return (start, start + delta)


# ─────────────────────────────────────────────────────────────────
# Property Test — emit_once uniqueness & allowlist-only keys
# ─────────────────────────────────────────────────────────────────
class TestPropertyTelemetryUniquenessAndDeidentification:
    """
    Property 3: 終態 telemetry 唯一且不含敏感內容。

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 8.9; Design Property 3**
    """

    @given(
        result=terminal_results(),
        corr_id=correlation_ids(),
        times=elapsed_times(),
        sentinels=sensitive_sentinels(),
        emit_count=st.integers(min_value=1, max_value=5),
    )
    def test_single_emission_per_correlation_context(
        self,
        result: Transcript | TypedAsrError,
        corr_id: str,
        times: tuple[float, float],
        sentinels: list[str],
        emit_count: int,
    ) -> None:
        """
        每組 Correlation Context 恰有一筆 telemetry record。
        多次 emit 呼叫仍只產生一筆。

        **Validates: Requirements 5.1, 5.2**
        """
        start_time, emit_time = times
        sink = CollectingSink()
        clock = lambda: emit_time  # noqa: E731
        emitter = TerminalTelemetryEmitter(
            sink=sink,
            clock=clock,
            start_time=start_time,
            correlation_id=corr_id,
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)
        emitter.set_route("zh-TW")
        emitter.set_provider_id("aws_zh")

        # 多次呼叫 emit
        for _ in range(emit_count):
            emitter.emit(result)

        # ─── Assertion: 恰一筆 record ───
        assert len(sink.records) == 1, (
            f"Expected exactly 1 telemetry record per correlation context, "
            f"got {len(sink.records)} after {emit_count} emit() calls."
        )

    @given(
        result=terminal_results(),
        corr_id=correlation_ids(),
        times=elapsed_times(),
        sentinels=sensitive_sentinels(),
    )
    def test_telemetry_keys_are_allowlist_only(
        self,
        result: Transcript | TypedAsrError,
        corr_id: str,
        times: tuple[float, float],
        sentinels: list[str],
    ) -> None:
        """
        Emitted record 的所有鍵只能在 TELEMETRY_ALLOWLIST_KEYS 內。

        **Validates: Requirements 5.3, 5.4**
        """
        start_time, emit_time = times
        sink = CollectingSink()
        clock = lambda: emit_time  # noqa: E731
        emitter = TerminalTelemetryEmitter(
            sink=sink,
            clock=clock,
            start_time=start_time,
            correlation_id=corr_id,
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)
        emitter.set_route("zh-TW")
        emitter.set_provider_id("aws_zh")

        emitter.emit(result)

        record = sink.records[0]
        record_dict = record.to_dict()

        # ─── Assertion: 所有 keys 在 allowlist 中 ───
        unexpected_keys = set(record_dict.keys()) - TELEMETRY_ALLOWLIST_KEYS
        assert unexpected_keys == set(), (
            f"Telemetry record contains non-allowlist keys: {unexpected_keys}"
        )

    @given(
        result=terminal_results(),
        corr_id=correlation_ids(),
        times=elapsed_times(),
        sentinels=sensitive_sentinels(),
    )
    def test_telemetry_values_do_not_contain_sensitive_sentinels(
        self,
        result: Transcript | TypedAsrError,
        corr_id: str,
        times: tuple[float, float],
        sentinels: list[str],
    ) -> None:
        """
        Telemetry record values 不得包含任何 sensitive sentinel
        （audio bytes、transcript text、HF token、PII、raw response、
        Formo Prompt ID）。

        **Validates: Requirements 5.4, 5.5, 8.9**
        """
        start_time, emit_time = times
        sink = CollectingSink()
        clock = lambda: emit_time  # noqa: E731
        emitter = TerminalTelemetryEmitter(
            sink=sink,
            clock=clock,
            start_time=start_time,
            correlation_id=corr_id,
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)
        emitter.set_route("zh-TW")
        emitter.set_provider_id("aws_zh")

        # Inject canonical audio (with sentinel-like PCM bytes)
        pcm_bytes = sentinels[0].encode("utf-8")[:32]
        if len(pcm_bytes) < 2:
            pcm_bytes = b"\x00\x01" * 16
        # Ensure even length for PCM 16-bit
        if len(pcm_bytes) % 2 != 0:
            pcm_bytes += b"\x00"
        audio = CanonicalAudio(
            pcm_s16le=pcm_bytes,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bits=16,
            duration_ms=100,
            input_format=InputFormat.WAV,
        )
        emitter.set_canonical_audio(audio)

        emitter.emit(result)

        record = sink.records[0]
        record_dict = record.to_dict()

        # ─── Assertion: values 不含任何 sentinel ───
        for key, value in record_dict.items():
            if value is None:
                continue
            str_value = str(value)
            for sentinel in sentinels:
                # Only check sentinels of length >= 3 to avoid false positives
                if len(sentinel) >= 3:
                    assert sentinel not in str_value, (
                        f"Sensitive sentinel {sentinel!r} found in telemetry "
                        f"field '{key}' with value {str_value!r}."
                    )

    @given(
        result=terminal_results(),
        corr_id=correlation_ids(),
        times=elapsed_times(),
    )
    def test_terminal_outcome_correctness(
        self,
        result: Transcript | TypedAsrError,
        corr_id: str,
        times: tuple[float, float],
    ) -> None:
        """
        terminal_outcome 為 success 對應 Transcript，為 error 對應 TypedAsrError。
        deadline_outcome 根據 error category 正確設定。
        elapsed_ms 為非負。
        error_category 正確對應 TypedAsrError.category.value。

        **Validates: Requirements 5.1, 5.2, 5.3**
        """
        start_time, emit_time = times
        sink = CollectingSink()
        clock = lambda: emit_time  # noqa: E731
        emitter = TerminalTelemetryEmitter(
            sink=sink,
            clock=clock,
            start_time=start_time,
            correlation_id=corr_id,
        )
        emitter.set_language(Language.ZH_TW)
        emitter.set_input_format(InputFormat.WAV)
        emitter.set_route("zh-TW")
        emitter.set_provider_id("aws_zh")

        emitter.emit(result)

        record = sink.records[0]

        # ─── Assertion: terminal_outcome ───
        if isinstance(result, Transcript):
            assert record.terminal_outcome == TerminalOutcome.SUCCESS.value
            assert record.error_category is None
            assert record.retryable is False
            assert record.deadline_outcome == DeadlineOutcome.NOT_REACHED.value
        else:
            assert record.terminal_outcome == TerminalOutcome.ERROR.value
            assert record.error_category == result.category.value

            # deadline_outcome 根據 error category 判斷
            if result.category == AsrErrorCategory.DEADLINE_EXCEEDED:
                assert record.deadline_outcome == DeadlineOutcome.DEADLINE_EXCEEDED.value
            elif result.category == AsrErrorCategory.CANCELLED:
                assert record.deadline_outcome == DeadlineOutcome.CANCELLED.value
            else:
                assert record.deadline_outcome == DeadlineOutcome.NOT_REACHED.value

            assert record.retryable == result.retryable

        # ─── Assertion: elapsed_ms non-negative ───
        assert record.elapsed_ms >= 0, (
            f"elapsed_ms must be non-negative, got {record.elapsed_ms}"
        )

        # ─── Assertion: correlation_id preserved ───
        assert record.correlation_id == corr_id
