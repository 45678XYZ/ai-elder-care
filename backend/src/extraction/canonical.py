"""Canonical 事件身分、受控詞彙與 `event_id` 生成器模組。

提供事件身分規範化、謂語/主體受控詞彙映射、時間桶 (Slot) 計算與確定性 `event_id` 產出。
架構規範與寫入規則詳見 `docs/framework.md` 的「Canonical identity 與寫入規則」。

本模組設計目的與核心機制：
- **伺服器端受控詞彙 (Server-Owned Controlled Lexicon)**：主體與謂語在組裝鍵值前必須經由伺服器端正規化。例如將「阿公」、「長者本人」統一收斂為「長者」，將「吃了降壓藥」、「服血壓藥」透過 `predicate_lexicon.json` 映射至相同的受控謂語，防止同一照護事實因口語表達差異而重複寫入。
- **三類事件 Canonical Key 分立**：
  1. 一般事件：`Date#Slot#Subject#Predicate`
  2. Routine 完成：`routine_completion#routine_id#routine_date`（刻意排除 `routine_version`，同日改版手動或對話完成均收斂至同一筆）
  3. 高風險 Safety：`SAFETY#session_id#episode`（收斂 Realtime 與 Batch 事件）
- **穩定確定性 `event_id` 產出**：由 `elder_id + canonical_event_key` 進行 SHA-256 雜湊產生。該識別碼與 Chunk 拆分方式、模型版本完全無關，能保證 SQS 批次作業 Retry 或 DLQ Replay 時為具備冪等性的覆蓋/去重寫入。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import hashlib
import json
import logging
import re
import unicodedata

from .config import TAXONOMY_ASSETS_DIR
from .taxonomy import Taxonomy
from .temporal import day_key, parse_ts

logger = logging.getLogger(__name__)

# 謂語受控詞彙檔名；儲存於 `taxonomy_assets` 目錄下
PREDICATE_LEXICON_FILE = "predicate_lexicon.json"

# 事件識別碼前綴
EVENT_ID_PREFIX = "evt_"

# 雜湊切除長度；與 `shared/db.py` 的 ID 產生機制一致，保持全系統二進制產出長度同步
EVENT_ID_HASH_LENGTH = 12

# Canonical Key 內部欄位分隔符號；採用 `#` 以配合 DynamoDB SK 複合鍵分割與前綴查詢
KEY_SEPARATOR = "#"
ROUTINE_KEY_PREFIX = "routine_completion"
SAFETY_KEY_PREFIX = "SAFETY"

# 預設事件主體；當模型未指明主體時預設對應長者本人
DEFAULT_SUBJECT = "長者"

# 句尾語助詞與標點符號正則；僅清理結尾無意義贅字（如「吃了血壓藥啦」->「吃了血壓藥」），保留句中語意用字
_TRAILING_PARTICLES = ("了", "啦", "喔", "唷", "哦", "呀", "呢", "耶", "吧", "嘛", "囉", "的")
_PUNCTUATION_PATTERN = re.compile(r"^[\s、,，.。!！?？~～\-—]+|[\s、,，.。!！?？~～\-—]+$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


class CanonicalError(ValueError):
    """Canonical Key 組成資料缺失、不合規或正規化失敗時拋出之例外。"""


@dataclass(frozen=True)
class ConceptPredicates:
    """單一概念 (Concept) 的受控謂語與別名對照資料結構。"""

    canonical: tuple[str, ...]
    aliases: dict[str, str]


@dataclass(frozen=True)
class PredicateLexicon:
    """伺服器管轄之主體與謂語受控詞彙庫模型。"""

    version: str
    other_token: str
    subject_aliases: dict[str, str]
    concepts: dict[str, ConceptPredicates]

    def candidates(self, concept_id: str) -> tuple[str, ...]:
        """取得特定概念的受控謂語候選清單。"""
        entry = self.concepts.get(concept_id)
        return entry.canonical if entry else ()

    def candidates_for_prompt(self, concept_ids: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        """組裝供 Prompt 使用的概念對應謂語白名單；未登記的概念回傳空元組。"""
        return {concept_id: self.candidates(concept_id) for concept_id in concept_ids}


@dataclass(frozen=True)
class PredicateResolution:
    """謂語正規化結果容器。"""

    value: str
    matched: bool
    matched_concept_id: str | None = None
    via_alias: bool = False
    via_fuzzy_embedding: bool = False
    similarity_score: float | None = None
    raw_predicate: str = ""


def load_predicate_lexicon(assets_dir: Path | str | None = None) -> PredicateLexicon:
    """載入謂語受控詞彙庫 JSON 資產。"""
    resolved = Path(assets_dir) if assets_dir is not None else TAXONOMY_ASSETS_DIR
    return _load_predicate_lexicon_cached(str(resolved.resolve()))


@lru_cache(maxsize=8)
def _load_predicate_lexicon_cached(assets_dir: str) -> PredicateLexicon:
    """以 `@lru_cache` 快取讀取 JSON 資產，避免批次處理時重複讀取磁碟。"""
    path = Path(assets_dir) / PREDICATE_LEXICON_FILE
    if not path.is_file():
        raise CanonicalError(f"謂語詞彙資產缺失：{path}")
    with path.open(encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    concepts: dict[str, ConceptPredicates] = {}
    for concept_id, entry in (raw.get("concepts") or {}).items():
        canonical = tuple(entry.get("canonical") or ())
        aliases = dict(entry.get("aliases") or {})
        if not canonical:
            raise CanonicalError(f"謂語詞彙缺少 canonical 清單：{concept_id}")
        for alias, target in aliases.items():
            if target not in canonical:
                raise CanonicalError(
                    f"謂語別名指向不存在的 canonical 值：{concept_id} {alias} -> {target}"
                )
        concepts[concept_id] = ConceptPredicates(canonical=canonical, aliases=aliases)

    return PredicateLexicon(
        version=raw.get("version") or "",
        other_token=raw.get("other_token") or "__other__",
        subject_aliases=dict(raw.get("subject_aliases") or {}),
        concepts=concepts,
    )


def validate_lexicon(lexicon: PredicateLexicon, taxonomy: Taxonomy) -> list[str]:
    """雙向校驗受控詞彙庫與概念樹 (Taxonomy) 之涵蓋一致性。

    確保所有的謂語概念均落在概念樹中，且所有的葉節點概念都有對應的謂語候選，防止部署缺失。
    """
    problems: list[str] = []
    for concept_id in lexicon.concepts:
        if taxonomy.get(concept_id) is None:
            problems.append(f"謂語詞彙指向不存在的節點：{concept_id}")
    for concept_id in taxonomy.leaf_ids():
        if concept_id not in lexicon.concepts:
            problems.append(f"葉節點缺少謂語候選：{concept_id}")
    return problems


def normalize_text(value: str) -> str:
    """字串標準化：進行 NFKC 全半形轉換、剔除空白、頭尾標點符號與尾端語助詞。"""
    text = unicodedata.normalize("NFKC", str(value))
    text = _WHITESPACE_PATTERN.sub("", text)
    text = _PUNCTUATION_PATTERN.sub("", text)
    while len(text) > 2:
        for particle in _TRAILING_PARTICLES:
            if text.endswith(particle):
                text = text[: -len(particle)]
                break
        else:
            break
    return text


def normalize_subject(
    subject: str | None,
    lexicon: PredicateLexicon,
    *,
    extra_aliases: Mapping[str, str] | None = None,
) -> str:
    """收斂事件主體。

    支援透過 `extra_aliases` 於執行期動態注入特定長者的專屬親友稱謂（如 `elders.family` 的暱稱映射），
    避免將個資或長者特有的家庭稱謂硬編碼於全域詞彙檔中。
    """
    text = normalize_text(subject or "")
    if not text:
        return DEFAULT_SUBJECT
    if extra_aliases:
        mapped = extra_aliases.get(text)
        if mapped:
            return normalize_text(mapped)
    return lexicon.subject_aliases.get(text, text)


def normalize_predicate(
    concept_id: str,
    predicate: str | None,
    lexicon: PredicateLexicon,
    taxonomy: Taxonomy | None = None,
    embedder: Any | None = None,
) -> PredicateResolution:
    """將口語謂語收斂至受控詞彙。

    比對順序：
    1. 該 concept 的 canonical 清單 (Exact Canonical Match)
    2. 該 concept 的別名表 (Alias Match)
    3. 沿祖先鏈重複上述比對
    4. 向量語義比對 (Embedding Fuzzy Match, sim >= 0.75)
    5. 未命中時保留正規化後的開放世界原字串 (Open-world Novel Predicate)，標記 matched=False。
    """
    raw_text = predicate or ""
    text = normalize_text(raw_text)
    if not text or text == lexicon.other_token:
        return PredicateResolution(value=text, matched=False, raw_predicate=raw_text)

    chain = [concept_id]
    if taxonomy is not None:
        chain.extend(taxonomy.ancestors(concept_id))

    for candidate_concept in chain:
        entry = lexicon.concepts.get(candidate_concept)
        if entry is None:
            continue
        if text in entry.canonical:
            return PredicateResolution(
                value=text, matched=True, matched_concept_id=candidate_concept, raw_predicate=raw_text
            )
        target = entry.aliases.get(text)
        if target:
            return PredicateResolution(
                value=target,
                matched=True,
                matched_concept_id=candidate_concept,
                via_alias=True,
                raw_predicate=raw_text,
            )

    # 向量語義模糊比對 (Fuzzy Embedding Match)
    if embedder is not None and hasattr(embedder, "embed"):
        candidates = lexicon.candidates(concept_id)
        if candidates:
            try:
                import numpy as np
                text_vec = embedder.embed(text)
                cand_vecs = [embedder.embed(c) for c in candidates]
                text_norm = text_vec / (np.linalg.norm(text_vec) + 1e-9)
                best_sim = -1.0
                best_cand = None
                for cand, vec in zip(candidates, cand_vecs):
                    vec_norm = vec / (np.linalg.norm(vec) + 1e-9)
                    sim = float(np.dot(text_norm, vec_norm))
                    if sim > best_sim:
                        best_sim = sim
                        best_cand = cand

                if best_cand and best_sim >= 0.65:
                    logger.info(
                        "謂語向量模糊比對命中：concept_id=%s %s -> %s (sim=%.3f)",
                        concept_id, text, best_cand, best_sim
                    )
                    return PredicateResolution(
                        value=best_cand,
                        matched=True,
                        matched_concept_id=concept_id,
                        via_fuzzy_embedding=True,
                        similarity_score=round(best_sim, 3),
                        raw_predicate=raw_text,
                    )
            except Exception as exc:
                logger.debug("向量模糊比對時出錯：%s", exc)

    logger.warning(
        "謂語未命中受控詞彙，沿用原值：concept_id=%s predicate=%s lexicon_version=%s",
        concept_id,
        text,
        lexicon.version,
    )
    return PredicateResolution(value=text, matched=False, raw_predicate=raw_text)


def slot_label(ts: str, slot_minutes: int) -> str:
    """依據 `EVENT_SLOT_MINUTES` 將時間戳分桶至固定邊界的時間區塊 (Slot)。

    計算公式：`slot_index = floor(minute_of_day / slot_minutes)`。
    當粒度為整小時倍數時輸出 `SLOT_HH`，其餘輸出 `SLOT_HHMM`。
    """
    if slot_minutes <= 0 or slot_minutes > 1440:
        raise CanonicalError(f"slot 粒度不合法：{slot_minutes}")
    moment = parse_ts(ts)
    slot_index = (moment.hour * 60 + moment.minute) // slot_minutes
    start_minute = slot_index * slot_minutes
    hour, minute = divmod(start_minute, 60)
    if slot_minutes % 60 == 0:
        return f"SLOT_{hour:02d}"
    return f"SLOT_{hour:02d}{minute:02d}"


def canonical_event_key(
    ts: str,
    subject: str,
    predicate: str,
    slot_minutes: int,
) -> str:
    """組合一般生活與照護事件的唯一身分鍵：`Date#Slot#Subject#Predicate`。"""
    subject_text = normalize_text(subject)
    predicate_text = normalize_text(predicate)
    if not subject_text or not predicate_text:
        raise CanonicalError("一般事件的 canonical key 需要 subject 與 predicate")
    for part in (subject_text, predicate_text):
        if KEY_SEPARATOR in part:
            raise CanonicalError(f"canonical key 組成不得包含分隔符號：{part}")
    return KEY_SEPARATOR.join((day_key(ts), slot_label(ts, slot_minutes), subject_text, predicate_text))


def routine_completion_key(routine_id: str, routine_date: str) -> str:
    """組合 Routine 完成事件的唯一身分鍵：`routine_completion#routine_id#routine_date`。

    刻意排除 `routine_version`，確保同一天內即便 Routine 定義修改，手動打卡或對話完成均收斂至同一筆實例。
    """
    if not routine_id or not routine_date:
        raise CanonicalError("routine completion 需要 routine_id 與 routine_date")
    return KEY_SEPARATOR.join((ROUTINE_KEY_PREFIX, routine_id, routine_date))


def safety_episode_key(session_id: str, episode: str) -> str:
    """組合高風險 Safety 事件的唯一身分鍵：`SAFETY#session_id#episode`。

    用於收斂同一對話輪次中 Realtime 即時告警與 Batch 批次萃取的重複事件。
    """
    if not session_id or not episode:
        raise CanonicalError("safety event 需要 session_id 與 episode")
    episode_text = normalize_text(episode)
    return KEY_SEPARATOR.join((SAFETY_KEY_PREFIX, session_id, episode_text))


def event_id_for(elder_id: str, canonical_key: str) -> str:
    """由 `elder_id + canonical_event_key` 經由 SHA-256 雜湊穩定生成唯一的 `event_id`。

    相同的長者與 Canonical Key 永遠產生完全相同的 ID，保障 SQS 批次處理與重試時具備寫入冪等性。
    """
    if not elder_id or not canonical_key:
        raise CanonicalError("event_id 需要 elder_id 與 canonical_event_key")
    signature = f"{elder_id}:{canonical_key}".encode("utf-8")
    digest = hashlib.sha256(signature).hexdigest()[:EVENT_ID_HASH_LENGTH]
    return f"{EVENT_ID_PREFIX}{digest}"


def event_time_key(ts: str, event_id: str) -> str:
    """生成 DynamoDB GSI `events-by-time` 的 Sort Key (`ts#event_id`)。

    要求 `ts` 必須為帶有固定毫秒精度的 ISO 8601 時間字串，確保 SK 字典排序完全對齊時間先後順序。
    """
    return f"{ts}{KEY_SEPARATOR}{event_id}"
