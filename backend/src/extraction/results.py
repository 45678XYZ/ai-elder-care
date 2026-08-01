"""Extraction Pipeline 的共同輸出型別與 LLM 呼叫記帳。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import CanonicalEvent, DedupStats

HIGH_LEVEL_TYPE_IDS: tuple[str, ...] = (
    "diet",
    "activity",
    "sleep",
    "medication",
    "wellbeing",
    "safety",
    "other",
)


@dataclass
class LlmUsage:
    """單一 session 執行期間的 LLM 呼叫記帳累積器。"""

    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    usage_missing_count: int = 0
    structured_output_degraded: int = 0

    def record(self, metadata: Mapping[str, Any]) -> None:
        self.call_count += 1

        usage = metadata.get("usage") if metadata else None
        input_tokens = usage.get("inputTokens") if isinstance(usage, Mapping) else None
        output_tokens = usage.get("outputTokens") if isinstance(usage, Mapping) else None

        if not isinstance(usage, Mapping) or input_tokens is None or output_tokens is None:
            self.usage_missing_count += 1

        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)
        self.latency_ms += int((metadata or {}).get("latency_ms") or 0)

        if metadata and metadata.get("structured_output") is False:
            self.structured_output_degraded += 1


@dataclass(frozen=True)
class PipelineResult:
    """Extraction Pipeline run() 的輸出型別。"""

    session_id: str
    pipeline_name: str
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats = field(default_factory=DedupStats)
    usage: LlmUsage = field(default_factory=LlmUsage)
    manifest: Any = None
    dropped_events: int = 0
    unmatched_predicates: int = 0
    stage_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def metrics(self) -> dict[str, Any]:
        type_distribution: dict[str, int] = dict.fromkeys(HIGH_LEVEL_TYPE_IDS, 0)
        for event in self.events:
            type_distribution[event.type] = type_distribution.get(event.type, 0) + 1

        common: dict[str, Any] = {
            "pipeline_name": self.pipeline_name,
            "event_count": len(self.events),
            "dropped_events": self.dropped_events,
            "unmatched_predicates": self.unmatched_predicates,
            "dedup_merge_rate": round(self.dedup.merge_rate, 4),
            "dedup_key_merged": self.dedup.key_merged,
            "dedup_alias_merged": self.dedup.alias_merged,
            "llm_call_count": self.usage.call_count,
            "llm_input_tokens": self.usage.input_tokens,
            "llm_output_tokens": self.usage.output_tokens,
            "llm_usage_missing_count": self.usage.usage_missing_count,
            "model_latency_ms": self.usage.latency_ms,
            "type_distribution": type_distribution,
        }

        merged = dict(self.stage_metrics)
        merged.update(common)
        return merged
