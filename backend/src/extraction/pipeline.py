"""Direct Seven Pipeline：不分塊、不檢索、單次（或依字元上限分批）七大類萃取。

整合設定、LLM 呼叫記帳、shared tail 收斂、與 pipeline 主入口。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
import json
import logging
import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from src.shared import bedrock
from src.shared.models import health_note_texts

from .canonical import (
    PredicateLexicon,
    build_family_aliases,
    canonical_event_key,
    event_id_for,
    normalize_predicate,
    normalize_subject,
)
from .dedup import deduplicate
from .models import CanonicalEvent, DedupStats, ExtractedEvent, Turn
from .taxonomy import Taxonomy
from .temporal import resolve_observed_at

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TAXONOMY_ASSETS_DIR = ASSETS_DIR / "taxonomy"

EXTRACTION_PROMPT_GUIDED = "prompt_guided"
EXTRACTION_STRUCTURED_OUTPUT = "structured_output"

HIGH_LEVEL_TYPE_IDS: tuple[str, ...] = (
    "diet",
    "activity",
    "sleep",
    "medication",
    "wellbeing",
    "safety",
    "other",
)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


@dataclass(frozen=True)
class ExtractionConfig:
    """一次 batch 執行所使用的萃取設定。"""

    event_slot_minutes: int = 30
    taxonomy_version: str | None = None
    extraction_mode: str = EXTRACTION_PROMPT_GUIDED

    model_id: str = ""
    extractor_model_id: str = ""

    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024

    seven_batch_char_limit: int = 12000

    batch_extractor_version: str = "batch-extractor-1"

    taxonomy_assets_dir: Path = field(default=TAXONOMY_ASSETS_DIR)

    def model_for(self, stage: str) -> str | None:
        """取某個階段要用的模型；回 None 代表交給 shared.bedrock 的預設。"""
        specific = {"extractor": self.extractor_model_id}.get(stage, "")
        return specific or self.model_id or None

    @classmethod
    def from_env(cls) -> "ExtractionConfig":
        return cls(
            event_slot_minutes=_env_int("EVENT_SLOT_MINUTES", 30),
            taxonomy_version=os.environ.get("TAXONOMY_VERSION", "").strip() or None,
            extraction_mode=_env_str("EXTRACTION_MODE", EXTRACTION_PROMPT_GUIDED),
            model_id=_env_str("BEDROCK_MODEL_ID", ""),
            extractor_model_id=_env_str("BEDROCK_EXTRACTOR_MODEL_ID", ""),
            embedding_model_id=_env_str("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
            embedding_dim=_env_int("EMBEDDING_DIM", 1024),
            seven_batch_char_limit=_env_int("SEVEN_BATCH_CHAR_LIMIT", 12000),
            batch_extractor_version=_env_str("BATCH_EXTRACTOR_VERSION", "batch-extractor-1"),
        )


# ---------------------------------------------------------------------------
# LLM Usage & PipelineResult
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Shared Tail
# ---------------------------------------------------------------------------

_MARKER_FIELDS: frozenset[str] = frozenset(
    {"classification_confidence", "raw_predicate", "suspected_routine_id"}
)

_EXCLUDED_GLOBAL_PROPERTIES: frozenset[str] = frozenset({"source_utterance"})

SuspectedRoutineLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class EventOrigin:
    """一批事件草稿的共同來源脈絡。"""

    reference_datetime: str
    evidence_conversation_ids: tuple[str, ...]
    source_chunk_id: str | None = None
    classification_confidence: float | None = None


@dataclass(frozen=True)
class _TailResult:
    events: tuple[CanonicalEvent, ...]
    dedup: DedupStats
    dropped_events: int = 0
    unmatched_predicates: int = 0


@dataclass
class _SharedTail:
    """有狀態尾段累積器：逐筆 absorb，最後一次 finalize。"""

    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    embedder: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None
    family_aliases: Mapping[str, str] = field(default_factory=dict)

    _drafts: list[CanonicalEvent] = field(default_factory=list, init=False, repr=False)
    _dropped_events: int = field(default=0, init=False, repr=False)
    _unmatched_predicates: int = field(default=0, init=False, repr=False)

    def absorb(
        self,
        *,
        elder_id: str,
        session_id: str,
        extracted: ExtractedEvent,
        origin: EventOrigin,
    ) -> bool:
        ts = resolve_observed_at(
            extracted.observed_at, extracted.raw_temporal_expression, origin.reference_datetime
        )

        ref_date = origin.reference_datetime.split("T")[0]
        ts_date = ts.split("T")[0]
        if ts_date < ref_date:
            logger.info(
                "過往歷史回憶事件已排除：ts=%s ref_date=%s concept_id=%s predicate=%s",
                ts, ref_date, extracted.concept_id, extracted.predicate,
            )
            self._dropped_events += 1
            return False

        subject = normalize_subject(extracted.subject, self.lexicon, extra_aliases=self.family_aliases)

        predicate = normalize_predicate(
            extracted.concept_id,
            extracted.predicate,
            self.lexicon,
            self.taxonomy,
            embedder=self.embedder,
        )
        if not predicate.value:
            logger.warning(
                "事件缺少可用謂語，已丟棄：source_chunk_id=%s concept_id=%s",
                origin.source_chunk_id,
                extracted.concept_id,
            )
            self._dropped_events += 1
            return False

        key = canonical_event_key(ts, subject, predicate.value, self.config.event_slot_minutes)
        structured = dict(extracted.attributes)

        hit_confidence = origin.classification_confidence
        if hit_confidence is not None:
            structured["classification_confidence"] = hit_confidence
        confidences = [
            value for value in (hit_confidence, extracted.confidence) if value is not None
        ]
        confidence = min(confidences) if confidences else None

        if predicate.raw_predicate and predicate.raw_predicate != predicate.value:
            structured["raw_predicate"] = predicate.raw_predicate

        if self.suspected_routine_lookup is not None:
            suspected = self.suspected_routine_lookup(extracted.concept_id, predicate.value, ts)
            if suspected:
                structured["suspected_routine_id"] = suspected

        evidence_ids = origin.evidence_conversation_ids
        event = CanonicalEvent(
            elder_id=elder_id,
            event_id=event_id_for(elder_id, key),
            canonical_event_key=key,
            ts=ts,
            type=self.taxonomy.high_level_type(extracted.concept_id),
            concept_id=extracted.concept_id,
            taxonomy_version=self.config.taxonomy_version or self.taxonomy.taxonomy_version,
            subject=subject,
            predicate=predicate.value,
            detail=extracted.summary,
            structured_detail=structured,
            confidence=confidence,
            session_id=session_id,
            source_chunk_id=origin.source_chunk_id,
            conversation_id=evidence_ids[0] if evidence_ids else None,
            evidence_conversation_ids=tuple(evidence_ids),
        )
        self._drafts.append(event)
        if not predicate.matched:
            self._unmatched_predicates += 1
        return predicate.matched

    def finalize(self) -> _TailResult:
        deduped_events, dedup_stats = deduplicate(
            self._drafts,
            slot_minutes=self.config.event_slot_minutes,
            lexicon=self.lexicon,
            embedder=self.embedder,
        )

        valid_events: list[CanonicalEvent] = []
        for event in deduped_events:
            if _validate_event(event, self.taxonomy):
                valid_events.append(event)
            else:
                logger.warning(
                    "事件未通過型別驗證，已丟棄：event_id=%s concept_id=%s type=%s",
                    event.event_id,
                    event.concept_id,
                    event.type,
                )
                self._dropped_events += 1

        return _TailResult(
            events=tuple(valid_events),
            dedup=dedup_stats,
            dropped_events=self._dropped_events,
            unmatched_predicates=self._unmatched_predicates,
        )


def _global_property_names(taxonomy: Taxonomy) -> frozenset[str]:
    names = {
        name
        for prop in (taxonomy.property_registry.get("global_properties") or [])
        if (name := prop.get("name")) and name not in _EXCLUDED_GLOBAL_PROPERTIES
    }
    return frozenset(names)


def _node_own_property_names(taxonomy: Taxonomy, concept_id: str) -> frozenset[str]:
    node_properties = taxonomy.property_registry.get("node_properties") or {}
    props = node_properties.get(concept_id)
    if not isinstance(props, list):
        return frozenset()
    return frozenset(name for prop in props if (name := prop.get("name")))


def _ancestor_chain_including_self(taxonomy: Taxonomy, concept_id: str) -> tuple[str, ...]:
    chain = [concept_id, *taxonomy.ancestors(concept_id)]
    ordered = tuple(reversed([cid for cid in chain if taxonomy.get(cid) is not None]))
    if ordered and taxonomy.nodes[ordered[0]].level == 0:
        return ordered[1:]
    return ordered


def _allowed_structured_detail_keys(taxonomy: Taxonomy, concept_id: str) -> frozenset[str]:
    if taxonomy.is_pseudo_concept(concept_id):
        return _MARKER_FIELDS
    allowed = set(_MARKER_FIELDS) | _global_property_names(taxonomy)
    for node_id in _ancestor_chain_including_self(taxonomy, concept_id):
        allowed |= _node_own_property_names(taxonomy, node_id)
    return frozenset(allowed)


def _validate_event(event: CanonicalEvent, taxonomy: Taxonomy) -> bool:
    if taxonomy.get(event.concept_id) is None:
        return False
    if event.type not in taxonomy.type_ids:
        return False
    if not (event.ts and event.subject and event.predicate and event.detail):
        return False
    allowed = _allowed_structured_detail_keys(taxonomy, event.concept_id)
    for key in event.structured_detail or {}:
        if key not in allowed:
            return False
    return True


# ---------------------------------------------------------------------------
# Seven Type Extraction (LLM call)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Turn Batching & Pipeline
# ---------------------------------------------------------------------------


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


def _render_batch_transcript(turns: Sequence[Turn]) -> str:
    return "\n".join(f"[{turn.created_at}] {turn.speaker}：{turn.text}" for turn in turns)


@dataclass
class DirectSevenPipeline:
    """不分塊、不檢索、單次（或依字元上限分批）七大類萃取。"""

    config: ExtractionConfig
    taxonomy: Taxonomy
    lexicon: PredicateLexicon
    client: Any = None
    embedder: Any = None
    suspected_routine_lookup: SuspectedRoutineLookup | None = None

    name: str = "direct_seven"

    def run(
        self,
        elder_id: str,
        session_id: str,
        session_snapshot_hash: str,
        turns: Sequence[Turn],
        *,
        elder: Mapping[str, Any] | None = None,
    ) -> PipelineResult:
        family_aliases = build_family_aliases(elder.get("family") if elder else None)
        tail = _SharedTail(
            config=self.config,
            taxonomy=self.taxonomy,
            lexicon=self.lexicon,
            embedder=self.embedder,
            suspected_routine_lookup=self.suspected_routine_lookup,
            family_aliases=family_aliases,
        )

        usage = LlmUsage()
        batches = plan_turn_batches(turns, self.config.seven_batch_char_limit)
        unmapped_type_count = 0
        dropped_events = 0

        for batch in batches:
            batch_turns = turns[batch.start : batch.end + 1]
            transcript = _render_batch_transcript(batch_turns)
            reference_datetime = batch_turns[-1].created_at
            evidence_ids = tuple(turn.conversation_id for turn in batch_turns)

            extraction = extract_seven_type_events(
                f"batch-{batch.ordinal}",
                transcript,
                reference_datetime,
                self.taxonomy,
                elder=elder,
                extraction_mode=self.config.extraction_mode,
                model_id=self.config.model_for("extractor"),
                client=self.client,
            )

            origin = EventOrigin(
                reference_datetime=reference_datetime,
                evidence_conversation_ids=evidence_ids,
                source_chunk_id=None,
                classification_confidence=None,
            )
            for extracted in extraction.events:
                tail.absorb(elder_id=elder_id, session_id=session_id, extracted=extracted, origin=origin)

            usage.record(extraction.metadata)
            unmapped_type_count += extraction.unmapped_type_count
            dropped_events += extraction.dropped_events

        tail_result = tail.finalize()

        return PipelineResult(
            session_id=session_id,
            pipeline_name=self.name,
            events=tail_result.events,
            dedup=tail_result.dedup,
            usage=usage,
            manifest=None,
            dropped_events=tail_result.dropped_events + dropped_events,
            unmatched_predicates=tail_result.unmatched_predicates,
            stage_metrics={
                "direct_seven_batch_count": len(batches),
                "unmapped_type_count": unmapped_type_count,
            },
        )
