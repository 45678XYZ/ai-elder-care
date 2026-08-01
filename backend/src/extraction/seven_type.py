"""七大類共用萃取：prompt、靜態 schema、單次 LLM 呼叫萃取、turn 邊界分批。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal
import json
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from src.shared import bedrock
from src.shared.models import health_note_texts

from .chunker import Turn
from .config import EXTRACTION_PROMPT_GUIDED, EXTRACTION_STRUCTURED_OUTPUT
from .models import ExtractedEvent
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

SEVEN_TYPE_EXTRACTOR_VERSION = "seven-type-extractor-1"
MAX_REPAIR_ATTEMPTS = 1

SYSTEM_PROMPT = (
    "你是長者照護資訊結構化萃取專家。只萃取長者已發生或正在發生的照護事實，"
    "保留具體數值與藥名等細節，不推測、不補充對話沒有的內容。"
    "AI 助理的建議、衛教提醒、風險警告不是事實行為，不要萃取。"
)


def build_elder_context(elder: Mapping[str, Any] | None) -> str:
    if not elder:
        return "（無長者背景資料）"

    lines: list[str] = []
    nickname = elder.get("nickname") or elder.get("name")
    if nickname:
        lines.append(f"- 稱謂：{nickname}")
    gender = elder.get("gender")
    if gender:
        gender_label = {"male": "男性", "female": "女性"}.get(gender, gender)
        lines.append(f"- 性別：{gender_label}")
    birth_year = elder.get("birth_year")
    if birth_year:
        lines.append(f"- 出生年份：{birth_year}")
    health_notes = health_note_texts(elder.get("health_notes"))
    if health_notes:
        lines.append(f"- 健康註記：{'、'.join(health_notes)}")
    habit_note = elder.get("habit_note")
    if habit_note:
        lines.append(f"- 生活習慣：{habit_note}")
    return "\n".join(lines) if lines else "（無長者背景資料）"


def build_seven_type_prompt(
    unit_id: str,
    transcript: str,
    reference_datetime: str,
    taxonomy: Taxonomy,
    *,
    elder: Mapping[str, Any] | None = None,
) -> str:
    type_lines = "\n".join(
        f"- `{type_.id}`（{type_.display_name}）：{type_.description}" for type_ in taxonomy.types
    )

    return f"""請從下列對話中萃取獨立的照護事件清單。

【事實判定原則 ★ 最重要】
1. 只萃取「長者已做、正在做、已經歷、已發生」的事件。
2. 以下全部不是事件，嚴禁萃取：
   - AI 助理的建議或衛教（如「建議冷敷」「記得多喝水」）
   - 長者的意圖或計劃（如「考慮今晚出門」「等下想去散步」）
   - 詢問行為（如「詢問是否要去散步」）
   - 準備動作（如「準備量測工具」），除非有後續的實際量測結果
   - AI 回應中的風險提醒或預防性衛教，不代表已發生不良事件
3. 判斷原則：如果把 event_summary 單獨讀出來，它能否告訴照護者「長者具體做了什麼/發生了什麼」？
   如果只是描述對話過程中的語言行為（提到/詢問/被建議），就不要萃取。

【事件分裂原則】
1. 對話中有多個獨立行為或量測（例如同時提到「量血壓 135/85」與「量體重 62 公斤」），
   或同一主題但時間點不同（例如「現在頭痛」與「昨天開始頭痛」），必須拆成多筆事件。
2. 同一件事只輸出一筆；主體不同或謂語不同才拆開。
3. `subject` 填事件主體（長者本人請填「長者」，其他人填其稱謂或姓名）。
4. `predicate` 填一個精簡的動作短語，用來辨識「這是哪一件事」。

【語境推理原則】
1. 必須結合上下文判斷行為的真正目的，不要只看單一詞彙。
2. AI 回應中若提到某種藥物的副作用風險，那是預防性衛教說明，不代表長者已發生該副作用。
3. 面對面在場交談與打電話是不同形式的互動，請根據對話語境正確判斷。

【時序推導】
基準時間 reference_datetime="{reference_datetime}"。
把每個事件的相對時間表達填進 `raw_temporal_expression`（如「昨天」「早上」），
並依基準時間推出 `observed_at` 的 ISO 8601 絕對時間（含 +08:00 時區）。
無法判斷時間就把兩個欄位都填 null，不要猜。

【長者背景】
{build_elder_context(elder)}

【允許的高階類別（high_level_type 只能從下列七類中選一個，無法歸類請選 `other`）】
{type_lines}

【輸出規則】
1. 只輸出符合下方 JSON 結構的 JSON，不要加說明文字或程式碼註解。
2. 未在對話中提及的欄位一律填 `null`，不要推測、不要沿用其他事件的值。
3. 同一件事只輸出一筆事件；不同主體或不同謂語則拆成多筆。
4. `event_summary` 必須描述已發生的事實結果，禁止使用「提到」「詢問」「被建議」「準備」「考慮」等過程性描述。
5. `subject` 與 `predicate` 必填，兩者決定事件身分。
6. `confidence_score` 必填，填 0.0–1.0 之間的浮點數。
7. 不需要填寫對話原文片段或證據位置，只需要以上欄位。

【萃取單位識別碼】
"{unit_id}"

【對話逐字稿】
{transcript}

請輸出 JSON 物件，包含 `"unit_id": "{unit_id}"` 與 `"events"` 陣列。
"""


@lru_cache(maxsize=8)
def _seven_type_event_model_cached(type_ids: tuple[str, ...]) -> type[BaseModel]:
    type_literal = Literal[type_ids]  # type: ignore[valid-type]
    return create_model(
        "SevenTypeEventItem",
        __config__=ConfigDict(extra="forbid"),
        event_index=(int, Field(description="事件在本批次內的序號，從 0 開始")),
        high_level_type=(
            type_literal,
            Field(description="事件所屬的高階類別"),
        ),
        subject=(str, Field(description="事件主體")),
        predicate=(str, Field(description="單一語意謂語")),
        event_summary=(str, Field(description="事件的自然語言精簡描述")),
        raw_temporal_expression=(
            str | None,
            Field(default=None, description="原始時間表達"),
        ),
        observed_at=(
            str | None,
            Field(default=None, description="ISO 8601 絕對時間"),
        ),
        confidence_score=(float, Field(description="信心值 0.0–1.0")),
    )


def seven_type_event_model(taxonomy: Taxonomy) -> type[BaseModel]:
    return _seven_type_event_model_cached(taxonomy.type_ids)


@lru_cache(maxsize=8)
def _seven_type_container_model_cached(type_ids: tuple[str, ...]) -> type[BaseModel]:
    event_model = _seven_type_event_model_cached(type_ids)
    return create_model(
        "SevenTypeExtractionContainer",
        __config__=ConfigDict(extra="forbid"),
        unit_id=(str, Field(description="萃取單位識別碼")),
        reference_datetime=(str, Field(description="時間對齊的參考基準")),
        events=(
            list[event_model],  # type: ignore[valid-type]
            Field(default_factory=list, description="事件清單"),
        ),
    )


def seven_type_container_model(taxonomy: Taxonomy) -> type[BaseModel]:
    return _seven_type_container_model_cached(taxonomy.type_ids)


@dataclass(frozen=True)
class SevenTypeExtraction:
    """單次 LLM 呼叫萃取七大類事件的輸出。"""

    events: tuple[ExtractedEvent, ...]
    dropped_events: int = 0
    unmapped_type_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_seven_type_events(
    unit_id: str,
    transcript: str,
    reference_datetime: str,
    taxonomy: Taxonomy,
    *,
    elder: Mapping[str, Any] | None = None,
    context_events: Sequence[Any] = (),
    extraction_mode: str = EXTRACTION_PROMPT_GUIDED,
    model_id: str | None = None,
    client=None,
) -> SevenTypeExtraction:
    prompt = build_seven_type_prompt(
        unit_id, transcript, reference_datetime, taxonomy, elder=elder
    )
    if context_events:
        lines = []
        for e in context_events:
            p = getattr(e, "predicate", "")
            d = getattr(e, "detail", "")
            t = getattr(e, "type", "other")
            if p or d:
                lines.append(f"- [{t}] {p}: {d}")
        if lines:
            context_block = (
                "\n\n【截至同 Session 前文已記錄之事件（請勿重複萃取相同行為與內容）】\n"
                + "\n".join(lines)
            )
            prompt += context_block
    event_model = seven_type_event_model(taxonomy)
    container_model = seven_type_container_model(taxonomy)

    if extraction_mode == EXTRACTION_STRUCTURED_OUTPUT:
        data, metadata = bedrock.converse_json(
            prompt,
            container_model.model_json_schema(),
            system=SYSTEM_PROMPT,
            model_id=model_id,
            schema_name="SevenTypeExtractionContainer",
            client=client,
        )
    else:
        text, metadata = bedrock.converse(
            prompt, system=SYSTEM_PROMPT, model_id=model_id, client=client
        )
        data = bedrock.extract_json(text)
        if not data:
            raise bedrock.RetryableBedrockError("七大類萃取輸出無法解析為 JSON 物件")

    raw_events = data.get("events")
    if not isinstance(raw_events, list):
        logger.warning("七大類萃取輸出缺少 events 陣列：unit_id=%s", unit_id)
        raw_events = []

    accepted, failures = _validate_seven_type_events(raw_events, event_model, taxonomy)

    repair_attempts = 0
    if failures and raw_events:
        repair_attempts = 1
        repaired_raw = _request_repair(
            prompt, failures, model_id=model_id, client=client, max_attempts=MAX_REPAIR_ATTEMPTS
        )
        if repaired_raw:
            repaired, still_failing = _validate_seven_type_events(repaired_raw, event_model, taxonomy)
            accepted.extend(repaired)
            failures = still_failing

    for failure in failures:
        logger.warning(
            "七大類事件驗證失敗已丟棄：unit_id=%s high_level_type=%s error=%s",
            unit_id,
            failure[0].get("high_level_type"),
            failure[1],
        )

    unmapped_type_count = 0
    events: list[ExtractedEvent] = []
    for index, (event, pseudo_concept_id, is_valid_label) in enumerate(accepted):
        if not is_valid_label:
            unmapped_type_count += 1
        events.append(_to_extracted_event(index, event, pseudo_concept_id))

    return SevenTypeExtraction(
        events=tuple(events),
        dropped_events=len(failures),
        unmapped_type_count=unmapped_type_count,
        metadata={
            **metadata,
            "extractor_version": SEVEN_TYPE_EXTRACTOR_VERSION,
            "repair_attempts": repair_attempts,
            "raw_event_count": len(raw_events),
        },
    )


def _validate_seven_type_events(
    raw_events: Sequence[Any],
    event_model: type[BaseModel],
    taxonomy: Taxonomy,
) -> tuple[list[tuple[dict[str, Any], str, bool]], list[tuple[dict[str, Any], str]]]:
    accepted: list[tuple[dict[str, Any], str, bool]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    model_fields = set(event_model.model_fields.keys())

    for raw in raw_events:
        if not isinstance(raw, dict):
            failures.append(({}, "事件不是物件"))
            continue
        cleaned = dict(raw)

        if cleaned.get("confidence_score") is None:
            cleaned["confidence_score"] = float(cleaned.get("confidence") or 1.0)

        pseudo_concept_id, is_valid_label = taxonomy.pseudo_concept_for_label(
            cleaned.get("high_level_type")
        )
        cleaned["high_level_type"] = (
            cleaned["high_level_type"] if is_valid_label else taxonomy.default_type
        )

        sanitized = {k: v for k, v in cleaned.items() if k in model_fields}

        try:
            validated = event_model.model_validate(sanitized)
        except ValidationError as exc:
            failures.append((raw, _summarize_validation_error(exc)))
            continue
        accepted.append((validated.model_dump(), pseudo_concept_id, is_valid_label))
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
        logger.warning("七大類事件修復重問失敗，改走丟棄路徑：%s", exc)
        return []

    repaired = bedrock.extract_json(text).get("events")
    return repaired if isinstance(repaired, list) else []


def _to_extracted_event(
    index: int,
    event: Mapping[str, Any],
    pseudo_concept_id: str,
) -> ExtractedEvent:
    confidence = event.get("confidence_score")
    return ExtractedEvent(
        concept_id=pseudo_concept_id,
        subject=str(event.get("subject", "")),
        predicate=str(event.get("predicate", "")),
        summary=str(event.get("event_summary", "")),
        attributes={},
        raw_temporal_expression=event.get("raw_temporal_expression"),
        observed_at=event.get("observed_at"),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        event_index=int(event.get("event_index", index)),
    )


@dataclass(frozen=True)
class TurnBatch:
    ordinal: int
    start: int
    end: int


def plan_turn_batches(turns: Sequence[Turn], char_limit: int) -> tuple[TurnBatch, ...]:
    """依 turn 邊界貪婪累積至 char_limit，切出連續且互不重疊的批次。"""
    if not turns:
        return ()

    batches: list[TurnBatch] = []
    batch_start = 0
    current_chars = 0

    for index, turn in enumerate(turns):
        turn_chars = len(f"{turn.speaker}：{turn.text}")
        if current_chars > 0 and current_chars + turn_chars > char_limit:
            batches.append(TurnBatch(ordinal=len(batches), start=batch_start, end=index - 1))
            batch_start = index
            current_chars = turn_chars
        else:
            current_chars += turn_chars

    batches.append(TurnBatch(ordinal=len(batches), start=batch_start, end=len(turns) - 1))
    return tuple(batches)
