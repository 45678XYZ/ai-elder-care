"""Single-pass 多事件萃取器。

移植自 aws-hackathon 的 `structured_extractor.extract_single_pass`（README 的 4 管道評測
支持 single_pass），保留「一次呼叫同時完成事件分裂、屬性萃取與時序推導」的設計，改四件事：

- **輸出新增 `subject`／`predicate`**。canonical key 由 `Date + Slot + Subject + Predicate`
  決定，少了這兩個欄位就算不出事件身分。
- **prompt 承載動態 schema 規則**（決策 H）。不走 grammar 硬約束，所以 per-concept 屬性白名單、
  predicate 候選與 null 政策都必須在 prompt 裡明列，來源是同一份 `schema_composer` 產物。
- **帶入長者 context**。hackathon 評測結論是萃取階段必須有 patient context，對應到這裡就是
  `elders` 的 persona 與 `health_notes`。
- **驗證失敗只丟該事件**（決策 I）。一次有界修復重問，仍失敗則丟棄並計數告警，
  不讓整個 chunk 變 `failed`。

不索取 `context_snippet`／`evidence_span`（決策 D）：追溯用 `evidence_conversation_ids`。
"""

from collections.abc import Mapping, Sequence
from typing import Any
import json
import logging

from pydantic import ValidationError

from src.shared import bedrock

from .config import EXTRACTION_STRUCTURED_OUTPUT
from .models import ComposedSchema, ExtractedEvent, ExtractionResult
from .schema_composer import describe_for_prompt, prune_irrelevant_event_properties
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# prompt 與輸出契約版本；寫進 metadata 供回溯
EXTRACTOR_VERSION = "single-pass-extractor-1"

# 一個 chunk 最多做幾次修復重問；有界才不會讓壞輸出拖垮 batch 延遲
MAX_REPAIR_ATTEMPTS = 1

SYSTEM_PROMPT = (
    "你是長者照護資訊結構化萃取專家。只萃取對話中明確提到的照護事實，"
    "保留具體數值與藥名等細節，不推測、不補充對話沒有的內容。"
)

# 事件物件裡屬於「事件本體」而非結構化屬性的欄位
_EVENT_CORE_FIELDS = frozenset(
    {
        "event_index",
        "concept_id",
        "subject",
        "predicate",
        "event_summary",
        "raw_temporal_expression",
        "observed_at",
    }
)


def build_elder_context(elder: Mapping[str, Any] | None) -> str:
    """把長者背景壓成 prompt 片段。

    只帶影響萃取判讀的欄位（暱稱、年齡、健康註記、生活習慣），不帶地址與親友姓名等
    對萃取無用的個資，符合 PII 最小化。
    """
    if not elder:
        return "（無長者背景資料）"

    lines: list[str] = []
    nickname = elder.get("nickname") or elder.get("name")
    if nickname:
        lines.append(f"- 稱謂：{nickname}")
    birth_year = elder.get("birth_year")
    if birth_year:
        lines.append(f"- 出生年份：{birth_year}")
    health_notes = elder.get("health_notes") or []
    if health_notes:
        lines.append(f"- 健康註記：{'、'.join(str(note) for note in health_notes)}")
    habit_note = elder.get("habit_note")
    if habit_note:
        lines.append(f"- 生活習慣：{habit_note}")
    return "\n".join(lines) if lines else "（無長者背景資料）"


def build_extraction_prompt(
    chunk_id: str,
    transcript: str,
    reference_datetime: str,
    composed: ComposedSchema,
    taxonomy: Taxonomy,
    *,
    predicate_candidates: Mapping[str, Sequence[str]] | None = None,
    elder: Mapping[str, Any] | None = None,
    other_predicate_token: str = "__other__",
) -> str:
    """組 single-pass 萃取 prompt。"""
    schema_rules = describe_for_prompt(
        composed,
        taxonomy,
        predicate_candidates=predicate_candidates,
        other_predicate_token=other_predicate_token,
    )

    return f"""請從下列對話塊中萃取獨立的照護事件清單。

【事件分裂原則】
1. 對話中有多個獨立行為或量測（例如同時提到「量血壓 135/85」與「量體重 62 公斤」），
   或同一主題但時間點不同（例如「現在頭痛」與「昨天開始頭痛」），必須拆成多筆事件。
2. 同一件事只輸出一筆；主體不同或謂語不同才拆開。
3. `subject` 填事件主體（長者本人請填「長者」，其他人填其稱謂或姓名）。
4. `predicate` 填單一語意謂語，用來辨識「這是哪一件事」。

【時序推導】
基準時間 reference_datetime="{reference_datetime}"。
把每個事件的相對時間表達填進 `raw_temporal_expression`（如「昨天」「早上」），
並依基準時間推出 `observed_at` 的 ISO 8601 絕對時間（含 +08:00 時區）。
無法判斷時間就把兩個欄位都填 null，不要猜。

【長者背景】
{build_elder_context(elder)}

{schema_rules}

【對話塊識別碼】
"{chunk_id}"

【對話逐字稿】
{transcript}

請輸出 JSON 物件，包含 `"chunk_id": "{chunk_id}"`、`"reference_datetime": "{reference_datetime}"` 與 `"events"` 陣列。
"""


def extract_events(
    chunk_id: str,
    transcript: str,
    reference_datetime: str,
    composed: ComposedSchema,
    taxonomy: Taxonomy,
    *,
    predicate_candidates: Mapping[str, Sequence[str]] | None = None,
    elder: Mapping[str, Any] | None = None,
    extraction_mode: str = "prompt_guided",
    model_id: str | None = None,
    client=None,
) -> ExtractionResult:
    """對單一 chunk 做 single-pass 萃取。"""
    prompt = build_extraction_prompt(
        chunk_id,
        transcript,
        reference_datetime,
        composed,
        taxonomy,
        predicate_candidates=predicate_candidates,
        elder=elder,
    )

    # 預設不走硬約束：動態 schema 每換一組標籤就是新 grammar，會反覆觸發首次編譯
    json_schema = composed.schema_json if extraction_mode == EXTRACTION_STRUCTURED_OUTPUT else None
    if json_schema is None:
        text, metadata = bedrock.converse(
            prompt, system=SYSTEM_PROMPT, model_id=model_id, client=client
        )
        data = bedrock.extract_json(text)
        if not data:
            # 整份解不開屬暫時性問題，交給上層重試而非丟掉整個 chunk
            raise bedrock.RetryableBedrockError("萃取輸出無法解析為 JSON 物件")
    else:
        data, metadata = bedrock.converse_json(
            prompt,
            json_schema,
            system=SYSTEM_PROMPT,
            model_id=model_id,
            schema_name="MultiEventExtraction",
            client=client,
        )

    raw_events = data.get("events")
    if not isinstance(raw_events, list):
        logger.warning("萃取輸出缺少 events 陣列：chunk_id=%s", chunk_id)
        raw_events = []

    accepted, failures = _validate_events(raw_events, composed)

    repair_attempts = 0
    if failures and raw_events:
        repair_attempts = 1
        repaired_raw = _request_repair(
            prompt, failures, model_id=model_id, client=client, max_attempts=MAX_REPAIR_ATTEMPTS
        )
        if repaired_raw:
            repaired, still_failing = _validate_events(repaired_raw, composed)
            accepted.extend(repaired)
            failures = still_failing
        # 修復後仍失敗者丟棄；chunk 其餘事件照寫（決策 I）

    for failure in failures:
        logger.warning(
            "事件驗證失敗已丟棄：chunk_id=%s concept_id=%s error=%s",
            chunk_id,
            failure[0].get("concept_id"),
            failure[1],
        )

    events = tuple(
        _to_extracted_event(index, event, composed)
        for index, event in enumerate(accepted)
    )
    return ExtractionResult(
        chunk_id=chunk_id,
        events=events,
        dropped_events=len(failures),
        metadata={
            **metadata,
            "extractor_version": EXTRACTOR_VERSION,
            "schema_fingerprint": composed.fingerprint,
            "repair_attempts": repair_attempts,
            "raw_event_count": len(raw_events),
        },
    )


def _validate_events(
    raw_events: Sequence[Any],
    composed: ComposedSchema,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """逐筆驗證事件，回傳 `(通過清單, 失敗清單)`。"""
    accepted: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            failures.append(({}, "事件不是物件"))
            continue
        try:
            validated = composed.event_model.model_validate(raw)
        except ValidationError as exc:
            failures.append((raw, _summarize_validation_error(exc)))
            continue
        # 驗證通過仍要清掉跨分類滲透的屬性；prompt 指引模式沒有解碼層保證
        accepted.append(prune_irrelevant_event_properties(validated.model_dump(), composed))
    return accepted, failures


def _summarize_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg')}")
    return "; ".join(parts)


def _request_repair(
    original_prompt: str,
    failures: Sequence[tuple[dict[str, Any], str]],
    *,
    model_id: str | None,
    client,
    max_attempts: int,
) -> list[Any]:
    """帶 validation error 做一次有界修復重問。

    只重問失敗的事件，通過的不重算——重算會讓已經正確的事件也承擔再壞一次的風險。
    """
    if max_attempts <= 0:
        return []

    problem_block = json.dumps(
        [{"event": failure[0], "error": failure[1]} for failure in failures],
        ensure_ascii=False,
        indent=2,
    )
    repair_prompt = (
        f"{original_prompt}\n\n"
        "【修復要求】\n"
        "上一次輸出中下列事件不符合 schema，請只修正這些事件後重新輸出。\n"
        "不要新增對話沒有提到的事件，無法修正的事件請直接省略。\n"
        f"```json\n{problem_block}\n```\n"
        '請輸出 {"events": [...]} 形式的 JSON 物件，只包含修正後的事件。'
    )

    try:
        text, _ = bedrock.converse(
            repair_prompt, system=SYSTEM_PROMPT, model_id=model_id, client=client
        )
    except bedrock.BedrockError as exc:
        # 修復是加分項；失敗就走丟棄路徑，不影響已通過的事件
        logger.warning("事件修復重問失敗，改走丟棄路徑：%s", exc)
        return []

    repaired = bedrock.extract_json(text).get("events")
    return repaired if isinstance(repaired, list) else []


def _to_extracted_event(
    index: int,
    event: Mapping[str, Any],
    composed: ComposedSchema,
) -> ExtractedEvent:
    attributes = {
        key: value
        for key, value in event.items()
        if key not in _EVENT_CORE_FIELDS and value is not None
    }
    confidence = attributes.pop("confidence_score", None)
    return ExtractedEvent(
        concept_id=str(event.get("concept_id", "")),
        subject=str(event.get("subject", "")),
        predicate=str(event.get("predicate", "")),
        summary=str(event.get("event_summary", "")),
        attributes=attributes,
        raw_temporal_expression=event.get("raw_temporal_expression"),
        observed_at=event.get("observed_at"),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        event_index=int(event.get("event_index", index)),
    )
