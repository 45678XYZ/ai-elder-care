"""Single-Pass 多事件萃取器模組。

提供對單一對話片段 (chunk) 進行多事件拆分、專屬屬性萃取與時序表達推導。
架構規範與設計決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

本模組設計目的與核心機制：
- **Single-Pass 高效萃取**：採用一次 LLM 呼叫同時完成事件拆分、屬性萃取與相對時間表達解析，大幅降低多輪呼叫的開銷與延遲。
- **基底身分欄位 mandatory 要求**：強制輸出 `subject`（主體，如「長者」）與 `predicate`（單一語意謂語，如「服用血壓藥」），作為後續計算 Canonical Event Key (`elder_id + Date + Slot + Subject + Predicate`) 的關鍵身分欄位。
- **單一源頭雙向 Schema 引導（決策 H）**：由 `schema_composer.py` 的產物導出完整的 Prompt 規則與 Pydantic 驗證模型。預設採用 Prompt 指引模式以避免動態 Schema 頻繁觸發 Bedrock 語法快取編譯。
- **長者背景情境注入**：注入長者 persona 與健康註記 (`health_notes`) 提供照護範疇引導，但嚴格排除住址等無關資訊以符合 PII 最小化。
- **有界修復與單一事件容錯（決策 I）**：驗證失敗時發起最多 1 次有界重問 (`MAX_REPAIR_ATTEMPTS = 1`) 修復問題事件。修復失敗僅丟棄該瑕疵事件並記錄告警，保障其餘正確事件正常寫入，不使整個 Chunk 失敗。
- **無原文片段落地（決策 D）**：不索取 `context_snippet` 或 `evidence_span`，對話追溯改用 `evidence_conversation_ids`。
"""

from collections.abc import Mapping, Sequence
from typing import Any
import json
import logging

from pydantic import ValidationError

from src.shared import bedrock
from src.shared.models import health_note_texts

from .config import EXTRACTION_STRUCTURED_OUTPUT
from .models import ComposedSchema, ExtractedEvent, ExtractionResult
from .schema_composer import describe_for_prompt, prune_irrelevant_event_properties
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 萃取器 Prompt 與輸出契約版本；寫入結果 metadata 供品質回溯與 A/B 測試比對
EXTRACTOR_VERSION = "single-pass-extractor-1"

# 單一 Chunk 進行 Validation Error 修復重問的上限（有界 1 次，防止瑕疵輸出拖垮批次任務延遲）
MAX_REPAIR_ATTEMPTS = 1

# 系統 Prompt：明確定位照護資訊結構化萃取專家，強制要求僅基於事實萃取，嚴禁推測與無中生有
SYSTEM_PROMPT = (
    "你是長者照護資訊結構化萃取專家。只萃取長者已發生或正在發生的照護事實，"
    "保留具體數值與藥名等細節，不推測、不補充對話沒有的內容。"
    "AI 助理的建議、衛教提醒、風險警告不是事實行為，不要萃取。"
)

# 事件物件中屬於身分與時序核心基底的欄位；用於與專屬動態屬性分隔
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
    """將長者個人背景與健康資料轉譯為 Prompt 脈絡片段。

    僅選取會直接影響萃取判讀與健康屬性理解的欄位（稱謂、出生年份、健康註記、生活習慣）；
    主動排除住址與親友姓名等對萃取無關之資訊，貫徹 PII 隱私最小化原則。
    """
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
    # health_notes 是物件陣列（含 note_id/source），prompt 只要文字
    health_notes = health_note_texts(elder.get("health_notes"))
    if health_notes:
        lines.append(f"- 健康註記：{'、'.join(health_notes)}")
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
    """組裝帶有動態 Schema 規則、長者背景與事件拆分原則的 Single-Pass 萃取提示詞。"""
    schema_rules = describe_for_prompt(
        composed,
        taxonomy,
        predicate_candidates=predicate_candidates,
        other_predicate_token=other_predicate_token,
    )

    return f"""請從下列對話塊中萃取獨立的照護事件清單。

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

{schema_rules}

【對話塊識別碼】
"{chunk_id}"

【對話逐字稿】
{transcript}

請輸出 JSON 物件，包含 `"chunk_id": "{chunk_id}"` 與 `"events"` 陣列。
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
    """對單一對話塊執行 Single-Pass 事件萃取、驗證與容錯處理。

    預設採用 Prompt 指引模式（不啟動 Bedrock 硬約束語法），避免每一次標籤組合變動
    引發靜態語法編譯延遲；若整體 JSON 損壞則拋出可重試例外，局部事件驗證失敗則走有界修復與容錯丟棄。
    """
    prompt = build_extraction_prompt(
        chunk_id,
        transcript,
        reference_datetime,
        composed,
        taxonomy,
        predicate_candidates=predicate_candidates,
        elder=elder,
    )

    # 預設不走硬約束：動態 schema 每換一組標籤就是新 grammar，會反覆觸發首次編譯延遲
    json_schema = composed.schema_json if extraction_mode == EXTRACTION_STRUCTURED_OUTPUT else None
    if json_schema is None:
        text, metadata = bedrock.converse(
            prompt, system=SYSTEM_PROMPT, model_id=model_id, client=client
        )
        data = bedrock.extract_json(text)
        if not data:
            # 整份 JSON 損壞屬網路或模型暫時性問題，拋出可重試例外供上層 SQS 重新派送
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
        # 修復後仍失敗之事件直接丟棄並告警，保住其餘驗證通過的合格事件（決策 I）

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
    """使用動態 Pydantic 模型逐筆驗證事件，並進行跨概念屬性防滲透過濾。

    傳回 `(通過驗證之事件清單, 失敗事件與原因清單)`。
    """
    accepted: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    model_fields = set(composed.event_model.model_fields.keys())

    for raw in raw_events:
        if not isinstance(raw, dict):
            failures.append(({}, "事件不是物件"))
            continue
        cleaned = dict(raw)

        # 1. 自動補齊 confidence_score，若模型漏填或誤填成 confidence
        if cleaned.get("confidence_score") is None:
            cleaned["confidence_score"] = float(cleaned.get("confidence") or 1.0)

        # 2. 剔除模型隨機吐出的非模型欄位 (如 quantity)，防止 extra='forbid' 誤丟事件
        sanitized = {k: v for k, v in cleaned.items() if k in model_fields}

        try:
            validated = composed.event_model.model_validate(sanitized)
        except ValidationError as exc:
            failures.append((raw, _summarize_validation_error(exc)))
            continue
        # 驗證通過後仍須清理非該概念白名單的跨概念滲透屬性（Prompt 指引模式下無網關解碼保證）
        accepted.append(prune_irrelevant_event_properties(validated.model_dump(), composed))
    return accepted, failures


def _summarize_validation_error(exc: ValidationError) -> str:
    """將 Pydantic ValidationError 摘要為單行文字說明。"""
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
    """帶入 Pydantic 驗證錯誤訊息，發起最多 1 次的有界修復重問。

    只對失敗的事件發起修正請求，已驗證通過的事件不重新計算，防止合格事件承擔再次出錯的風險。
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
        # 重問修復屬於加分項；若呼叫失敗則記錄告警並直接丟棄失敗事件，不損害已通過事件
        logger.warning("事件修復重問失敗，改走丟棄路徑：%s", exc)
        return []

    repaired = bedrock.extract_json(text).get("events")
    return repaired if isinstance(repaired, list) else []


def _to_extracted_event(
    index: int,
    event: Mapping[str, Any],
    composed: ComposedSchema,
) -> ExtractedEvent:
    """將通過驗證的原始字典轉換為標準的 ExtractedEvent 資料模型實例。

    將屬性拆分為核心欄位（subject, predicate, summary, observed_at）與專屬動態屬性字典 (attributes)，
    供後續模組對應寫入 DynamoDB 的 `events` 資料表。
    """
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
