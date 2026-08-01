"""CloudWatch 指標（Embedded Metric Format）。

用 EMF 而不是 `PutMetricData`：EMF 只是把結構化 JSON 寫到 stdout，CloudWatch Logs 會自動
解析成指標。好處是零額外 API 呼叫、零 IAM 權限、不佔 Lambda 執行時間，也不會因為指標寫入
失敗而拖垮業務邏輯——對 batch worker 特別重要，觀測不該成為新的失敗點。

要觀測什麼，見 docs/framework.md 的「成本與可觀測性」：這條 pipeline 的成本與品質都是推論
出來的，未經量測不承諾數字。因此關鍵訊號一律要有指標，而不是只寫 log：

- chunk 數與 fallback 比例：分塊器是不是其實一直在走機械切分
- 去重合併率：canonical key 設計有沒有真的收斂重複
- 事件分類分佈：某一類長期為 0 通常代表 prompt 或映射有問題
- 丟棄事件數與 predicate 未命中數：品質退化的早期訊號
- structured output 降級次數：模型或 SDK 不支援硬約束時要看得到
- batch claim 的處置分佈：重複投遞、lease 接管、失敗各佔多少
"""

from collections.abc import Mapping, Sequence
from typing import Any
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("METRICS_NAMESPACE", "AiElderCare/Extraction")

# 指標名稱集中管理，避免各處拼字不一致而變成兩個指標
EVENT_COUNT = "EventCount"
DROPPED_EVENTS = "DroppedEvents"
UNMATCHED_PREDICATES = "UnmatchedPredicates"
DEDUP_MERGE_RATE = "DedupMergeRate"
DEDUP_KEY_MERGED = "DedupKeyMerged"
DEDUP_ALIAS_MERGED = "DedupAliasMerged"
STRUCTURED_OUTPUT_DEGRADED = "StructuredOutputDegraded"
MODEL_LATENCY = "ModelLatencyMs"
BATCH_ATTEMPTS = "BatchAttempts"
BATCH_OUTCOME = "BatchOutcome"
BATCH_DURATION = "BatchDurationMs"
EVENTS_BY_TYPE = "EventsByType"
DLQ_OUTCOME = "DlqOutcome"
SESSION_SWEEP = "SessionSweep"

# 每日摘要：partial 比例與重算次數是 framework 成本章節明列要觀測的項目
SUMMARY_GENERATED = "SummaryGenerated"
SUMMARY_PENDING_SESSIONS = "SummaryPendingSessions"
SUMMARY_MODEL_LATENCY = "SummaryModelLatencyMs"
SUMMARY_WRITE_REJECTED = "SummaryWriteRejected"
SUMMARY_REGENERATED = "SummaryRegenerated"

# 單位不是 Count 的指標；其餘預設 Count
_UNITS: dict[str, str] = {
    MODEL_LATENCY: "Milliseconds",
    BATCH_DURATION: "Milliseconds",
    SUMMARY_MODEL_LATENCY: "Milliseconds",
    DEDUP_MERGE_RATE: "Percent",
}


def enabled() -> bool:
    """可關閉：本機測試與單元測試不需要噴 EMF 到 stdout。"""
    return os.environ.get("METRICS_ENABLED", "true").lower() not in ("0", "false", "no")


def emit(
    values: Mapping[str, float],
    *,
    dimensions: Mapping[str, str] | None = None,
    properties: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """送出一組指標；回傳實際寫出的 EMF 物件（測試用），停用時回 None。

    `dimensions` 會成為 CloudWatch 的維度（基數要低，別放 session_id 這類高基數值）；
    `properties` 只進 log 不成為維度，適合放 session_id 供事後追查。
    """
    if not values or not enabled():
        return None

    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [sorted(dimensions)] if dimensions else [[]],
                    "Metrics": [
                        {"Name": name, "Unit": _UNITS.get(name, "Count")} for name in values
                    ],
                }
            ],
        }
    }
    payload.update(dimensions or {})
    payload.update(properties or {})
    for name, value in values.items():
        payload[name] = float(value)

    # 直接 print：EMF 需要獨立一行的 JSON，logging 的前綴會讓 CloudWatch 解析失敗
    print(json.dumps(payload, ensure_ascii=False))
    return payload


def emit_pipeline_metrics(
    pipeline_metrics: Mapping[str, Any],
    *,
    elder_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """把 `PipelineResult.metrics` 送成指標。

    事件分類分佈另外一筆一筆送（維度 `EventType`），這樣才能在 CloudWatch 上分類別看趨勢；
    高基數的 `concept_id` 不進維度，只留在 log properties。
    """
    type_distribution = pipeline_metrics.get("type_distribution") or {}
    properties = {
        key: value
        for key, value in (("elder_id", elder_id), ("session_id", session_id))
        if value
    }

    emit(
        {
            EVENT_COUNT: pipeline_metrics.get("event_count", 0),
            DROPPED_EVENTS: pipeline_metrics.get("dropped_events", 0),
            UNMATCHED_PREDICATES: pipeline_metrics.get("unmatched_predicates", 0),
            DEDUP_MERGE_RATE: float(pipeline_metrics.get("dedup_merge_rate", 0.0)) * 100,
            DEDUP_KEY_MERGED: pipeline_metrics.get("dedup_key_merged", 0),
            DEDUP_ALIAS_MERGED: pipeline_metrics.get("dedup_alias_merged", 0),
            STRUCTURED_OUTPUT_DEGRADED: pipeline_metrics.get("structured_output_degraded", 0),
            MODEL_LATENCY: pipeline_metrics.get("model_latency_ms", 0),
        },
        properties=properties,
    )

    for event_type, count in sorted(type_distribution.items()):
        emit({EVENTS_BY_TYPE: count}, dimensions={"EventType": event_type}, properties=properties)


def emit_batch_outcome(
    outcome: str,
    *,
    attempts: int | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
) -> None:
    """batch claim 的處置分佈。

    重複投遞、lease 接管、snapshot 過期、失敗各自都是正常會發生的事；
    要看的是**比例變化**，例如 lease 接管突然變多通常代表 worker 在超時。
    """
    values: dict[str, float] = {BATCH_OUTCOME: 1}
    if attempts is not None:
        values[BATCH_ATTEMPTS] = attempts
    if duration_ms is not None:
        values[BATCH_DURATION] = duration_ms
    emit(
        values,
        dimensions={"Outcome": outcome},
        properties={"session_id": session_id} if session_id else None,
    )


def emit_dlq_outcome(outcome: str, *, session_id: str | None = None) -> None:
    emit(
        {DLQ_OUTCOME: 1},
        dimensions={"Outcome": outcome},
        properties={"session_id": session_id} if session_id else None,
    )


def emit_sweep_result(results: Mapping[str, int]) -> None:
    """週期性 sweep 的處理量。

    這些數字長期不為 0 代表有系統性問題：`pending_requeued` 持續 > 0 表示 close 之後的
    SendMessage 常常失敗；`processing_requeued` 持續 > 0 表示 worker 常常死在中途。
    """
    for name, count in sorted(results.items()):
        emit({SESSION_SWEEP: count}, dimensions={"SweepKind": name})


def summarize_units(names: Sequence[str]) -> dict[str, str]:
    """給文件與測試用：列出指標對應的單位。"""
    return {name: _UNITS.get(name, "Count") for name in names}
