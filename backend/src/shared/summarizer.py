"""每日摘要生成（`POST /summaries/generate` 與排程 generator 共用）。

規範見 docs/api.md 的「每日摘要」與 docs/framework.md 的 `daily_summaries` 表；
設計脈絡見 docs/feature_daily-summarization.md。

分工是這個模組唯一重要的事：**可計算的事實由程式算，自然語言由模型寫**。
`interaction_count`、`pending_session_count`、`data_status`、`routines` 的統計都有唯一正確
答案且可驗證，交給模型只會引入無法重現的錯誤；反過來，摘要文字沒有唯一答案，適合模型。

摘要是衍生快照：不回寫 event、不回寫 routine 狀態，重算一律從來源資料重新算。
"""

from datetime import datetime, timedelta
from typing import Any
import logging
import os

from src.extraction.temporal import TZ_TAIPEI, day_key, format_ts, parse_ts
from src.shared import bedrock, db, metrics
from src.shared import routines as routines_module
from src.shared import sessions
from src.shared.models import SUMMARY_SECTION_KEYS

logger = logging.getLogger(__name__)

DATA_STATUS_COMPLETE = "complete"
DATA_STATUS_PARTIAL = "partial"

DEFAULT_GENERATOR_VERSION = "summary-generator-1"
DEFAULT_ALERT_LOOKBACK_DAYS = 7
DEFAULT_MAX_EVENTS = 120

# 沒有任何資料的日子用固定文字，不呼叫模型：空輸入只會讓模型編故事
EMPTY_OVERVIEW = "今日沒有對話紀錄，也沒有需要追蹤的行程。"

# alerts 只看這兩類事件的跨日趨勢；其餘類別的長期變化屬統計而非警訊
ALERT_EVENT_TYPES: tuple[str, ...] = ("safety", "wellbeing")

SYSTEM_PROMPT = (
    "你是長照紀錄摘要助理，替家屬與照護者整理長者一天的生活紀錄。"
    "只根據提供的事件與行程撰寫，不得推測或補充未提供的資訊，"
    "不做醫療診斷、不給治療建議。用溫和、具體、好讀的台灣繁體中文，"
    "每個分類兩句以內；該分類沒有資料就填 null，不要寫「無」或「沒有資料」。"
)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def generator_version() -> str:
    return os.environ.get("SUMMARY_GENERATOR_VERSION", "").strip() or DEFAULT_GENERATOR_VERSION


def summary_model_id() -> str | None:
    """摘要階段的模型；留空沿用主模型（再留空則是 `shared.bedrock` 的預設）。"""
    staged = os.environ.get("BEDROCK_SUMMARY_MODEL_ID", "").strip()
    return staged or os.environ.get("BEDROCK_MODEL_ID", "").strip() or None


def content_schema() -> dict[str, Any]:
    """模型輸出的硬約束 schema。

    七個 section key 由 `SUMMARY_SECTION_KEYS`（= `EventType`）產生而不是寫死字串：
    新增高階類別時 schema 自動跟上，不會出現「events 有這一類、摘要卻沒有」的落差。
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string", "description": "當日總覽，三句以內"},
            "sections": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    key: {
                        "type": ["string", "null"],
                        "description": f"{key} 類事件的摘要；沒有資料填 null",
                    }
                    for key in SUMMARY_SECTION_KEYS
                },
                "required": list(SUMMARY_SECTION_KEYS),
            },
            "alerts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "需要照護者注意的訊號；沒有就給空陣列",
            },
        },
        "required": ["overview", "sections", "alerts"],
    }


# -----------------------------------------------------------------------------
# 事實蒐集
# -----------------------------------------------------------------------------


def collect_day_events(elder_id: str, date: str, *, max_events: int | None = None) -> list[dict[str, Any]]:
    """取當日事件，時間正序。

    上限是防呆：單日事件量正常在數十筆，但萃取出錯時可能爆量，不能讓 prompt 無上限成長。
    截斷時保留較早的事件並記 log——截尾比截頭好，因為摘要是按時間敘事。
    """
    limit = max_events if max_events is not None else _env_int("SUMMARY_MAX_EVENTS", DEFAULT_MAX_EVENTS)
    collected: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        items, next_token = db.list_events(
            elder_id, from_date=date, to_date=date, limit=100, next_token=next_token
        )
        collected.extend(items)
        if not next_token:
            break

    # list_events 是最新優先；摘要要按時間敘事，因此轉為正序
    collected.sort(key=lambda item: (item.get("ts") or "", item.get("event_id") or ""))
    if len(collected) > limit:
        logger.warning(
            "當日事件數 %s 超過上限 %s，截斷後生成摘要：elder_id=%s date=%s",
            len(collected),
            limit,
            elder_id,
            date,
        )
        collected = collected[:limit]
    return collected


def collect_recent_signals(elder_id: str, date: str, *, lookback_days: int | None = None) -> list[dict[str, Any]]:
    """取近幾日的 safety／wellbeing 事件，供 alerts 判斷跨日趨勢（不含當日）。"""
    days = lookback_days if lookback_days is not None else _env_int(
        "SUMMARY_ALERT_LOOKBACK_DAYS", DEFAULT_ALERT_LOOKBACK_DAYS
    )
    if days <= 1:
        return []
    start = day_key(parse_ts(f"{date}T00:00:00+08:00") - timedelta(days=days - 1))
    previous_day = day_key(parse_ts(f"{date}T00:00:00+08:00") - timedelta(days=1))
    if start > previous_day:
        return []

    signals: list[dict[str, Any]] = []
    for event_type in ALERT_EVENT_TYPES:
        next_token: str | None = None
        while True:
            items, next_token = db.list_events(
                elder_id,
                from_date=start,
                to_date=previous_day,
                event_type=event_type,
                limit=50,
                next_token=next_token,
            )
            signals.extend(items)
            if not next_token:
                break
    signals.sort(key=lambda item: item.get("ts") or "")
    return signals


def count_interactions_and_pending(elder_id: str, date: str) -> tuple[int, list[dict[str, Any]]]:
    """當日 `/chat` turn 數與仍待 materialize 的 session。

    `interaction_count` 只計 `request_status=completed`：failed turn 保證沒有業務副作用，
    算進互動次數會讓照護者看到不存在的互動。session 候選來自同一次 GSI 查詢，狀態則由
    `sessions.list_pending_sessions` 回 Base table 強一致判斷。
    """
    turns = db.list_turns_by_day(elder_id, date)
    completed = [turn for turn in turns if turn.get("request_status") == "completed"]
    session_ids = [turn.get("session_id") for turn in turns if turn.get("session_id")]
    pending = sessions.list_pending_sessions(elder_id, session_ids)
    return len(completed), pending


# -----------------------------------------------------------------------------
# 模型輸入與輸出
# -----------------------------------------------------------------------------


def build_prompt(
    date: str,
    events: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    recent_signals: list[dict[str, Any]],
    *,
    data_status: str,
) -> str:
    """組 prompt。只給事件的 `detail`／`structured_detail`，不給逐字稿。

    不放逐字稿有兩個理由：PII 擴散面要小（framework 明文「不複製逐字稿」），
    而且 `detail` 已是萃取後的完整事件描述，逐字稿只會稀釋訊號。
    """
    lines = [f"日期：{date}", ""]

    lines.append("【當日事件】")
    if events:
        for item in events:
            detail = item.get("detail") or ""
            structured = item.get("structured_detail") or {}
            extra = (
                "（" + "、".join(f"{key}={value}" for key, value in sorted(structured.items())) + "）"
                if structured
                else ""
            )
            lines.append(f"- {item.get('ts', '')} [{item.get('type', 'other')}] {detail}{extra}")
    else:
        lines.append("- （無）")

    lines += ["", "【當日行程與完成狀態】"]
    if occurrences:
        for item in occurrences:
            suffix = f"，完成於 {item['completed_at']}" if item.get("completed_at") else ""
            lines.append(
                f"- {item.get('scheduled_at', '')} {item.get('title', '')}"
                f"：{item.get('status')}{suffix}"
            )
    else:
        lines.append("- （無）")

    if recent_signals:
        lines += ["", "【近幾日的健康與安全事件（供判斷是否為持續趨勢）】"]
        for item in recent_signals:
            lines.append(f"- {item.get('ts', '')} [{item.get('type')}] {item.get('detail', '')}")

    if data_status == DATA_STATUS_PARTIAL:
        lines += [
            "",
            "注意：今天還有對話尚未整理完成，資料不完整。"
            "總覽請說明是「截至目前已整理的資料」，不要寫成一整天的定論。",
        ]

    lines += [
        "",
        "請依上述資料填寫 overview、sections 七類與 alerts。",
        "alerts 只放需要照護者注意的訊號（例如反覆疼痛、跌倒、情緒低落、應完成卻未完成的用藥）；",
        "沒有就給空陣列。行程完成統計與互動次數由系統計算，不要在文字裡重複數字。",
    ]
    return "\n".join(lines)


def normalize_content(data: dict[str, Any]) -> dict[str, Any]:
    """把模型輸出收斂成契約形狀。

    七個 key 一律完整存在、缺值為 `None`（api.md：無資料為 null）；模型多給的 key 直接丟掉，
    否則 `sections` 會出現前端不認識的欄位。
    """
    raw_sections = data.get("sections") or {}
    sections: dict[str, str | None] = {}
    for key in SUMMARY_SECTION_KEYS:
        value = raw_sections.get(key)
        text = str(value).strip() if isinstance(value, str) else ""
        sections[key] = text or None

    unexpected = sorted(set(raw_sections) - set(SUMMARY_SECTION_KEYS))
    if unexpected:
        logger.warning("模型輸出含未定義的 section：%s", unexpected)

    alerts_raw = data.get("alerts") or []
    alerts = [str(item).strip() for item in alerts_raw if str(item).strip()] if isinstance(alerts_raw, list) else []

    overview = str(data.get("overview") or "").strip()
    return {"overview": overview, "sections": sections, "alerts": alerts}


def empty_content() -> dict[str, Any]:
    return {
        "overview": EMPTY_OVERVIEW,
        "sections": {key: None for key in SUMMARY_SECTION_KEYS},
        "alerts": [],
    }


def generate_content(
    date: str,
    events: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    recent_signals: list[dict[str, Any]],
    *,
    data_status: str,
    client=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """呼叫模型產生 overview／sections／alerts；無資料時不呼叫模型。"""
    if not events and not occurrences:
        return empty_content(), {}

    prompt = build_prompt(date, events, occurrences, recent_signals, data_status=data_status)
    data, metadata = bedrock.converse_json(
        prompt,
        content_schema(),
        system=SYSTEM_PROMPT,
        model_id=summary_model_id(),
        schema_name="DailySummary",
        max_tokens=2048,
        client=client,
    )
    return normalize_content(data), metadata


# -----------------------------------------------------------------------------
# 入口
# -----------------------------------------------------------------------------


def build_summary(
    elder_id: str,
    date: str,
    *,
    input_through_at: str | datetime | None = None,
    client=None,
) -> dict[str, Any]:
    """算出一份完整的摘要 item（尚未寫入）。

    `input_through_at` 是「本摘要承諾納入的資料時間上限」，也是覆寫優先序的依據；
    未提供時取現在時間。它同時是 routine occurrence 的 cutoff，摘要與行程狀態才會一致。
    """
    cutoff = format_ts(parse_ts(input_through_at) if input_through_at else datetime.now(TZ_TAIPEI))

    interaction_count, pending = count_interactions_and_pending(elder_id, date)
    occurrences = routines_module.list_occurrences(elder_id, date, cutoff=cutoff)
    events = collect_day_events(elder_id, date)
    data_status = DATA_STATUS_COMPLETE if not pending else DATA_STATUS_PARTIAL
    recent_signals = collect_recent_signals(elder_id, date)

    content, model_metadata = generate_content(
        date, events, occurrences, recent_signals, data_status=data_status, client=client
    )

    summary = {
        "elder_id": elder_id,
        "date": date,
        "overview": content["overview"],
        "sections": content["sections"],
        "routines": routines_module.summary_snapshot(occurrences),
        "alerts": content["alerts"],
        "interaction_count": interaction_count,
        "data_status": data_status,
        "pending_session_count": len(pending),
        "input_through_at": cutoff,
        "generated_at": format_ts(datetime.now(TZ_TAIPEI)),
        "generator_version": generator_version(),
        "schema_version": 1,
    }
    summary["_model_metadata"] = model_metadata
    return summary


def generate_and_store(
    elder_id: str,
    date: str,
    *,
    input_through_at: str | datetime | None = None,
    client=None,
) -> tuple[dict[str, Any], bool]:
    """生成並以覆寫優先序條件式寫入；回 `(目前生效的摘要, 是否由本次寫入)`。

    條件不成立代表既有摘要更新或更完整，屬正常情形（例如手動 partial 撞上排程 complete）；
    此時回傳既有摘要，呼叫端據此回應，不視為錯誤。
    """
    summary = build_summary(elder_id, date, input_through_at=input_through_at, client=client)
    model_metadata = summary.pop("_model_metadata", {})

    stored, written = db.put_daily_summary(summary)
    _emit_metrics(summary, written=written, model_metadata=model_metadata)
    return stored, written


def _emit_metrics(summary: dict[str, Any], *, written: bool, model_metadata: dict[str, Any]) -> None:
    values: dict[str, float] = {
        metrics.SUMMARY_GENERATED: 1,
        metrics.SUMMARY_PENDING_SESSIONS: summary["pending_session_count"],
        metrics.SUMMARY_WRITE_REJECTED: 0 if written else 1,
    }
    latency = model_metadata.get("latency_ms")
    if latency is not None:
        values[metrics.SUMMARY_MODEL_LATENCY] = latency
    metrics.emit(
        values,
        dimensions={"DataStatus": summary["data_status"]},
        properties={"elder_id": summary["elder_id"], "date": summary["date"]},
    )
