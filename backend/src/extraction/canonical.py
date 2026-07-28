"""Canonical 事件身分：Slot、Subject、Predicate 與 `event_id`。

規範見 docs/framework.md 的「Canonical identity 與寫入規則」。三種事件的 canonical key 各有規則：

- 一般事件：`Date + Slot + Subject + Predicate`
- routine 完成：只由 `routine_id + routine_date` 決定（不含 `routine_version`，同日改版仍是同一筆）
- 高風險 safety：由 `session_id + alert episode` 決定

`event_id` 一律由 `elder_id + canonical_event_key` 穩定產生，與 chunk、track、模型版本無關。
Subject 與 Predicate 都必須先經 server-owned 正規化再組 key，否則同一件事會因表述不同而重複。
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

PREDICATE_LEXICON_FILE = "predicate_lexicon.json"

EVENT_ID_PREFIX = "evt_"
# 與既有 shared/db.py 的產生規則一致，避免同一份資料在兩處算出不同 ID
EVENT_ID_HASH_LENGTH = 12

KEY_SEPARATOR = "#"
ROUTINE_KEY_PREFIX = "ROUTINE"
SAFETY_KEY_PREFIX = "SAFETY"

DEFAULT_SUBJECT = "長者"

# 句尾語助詞與標點；只清尾端，避免動到「吃了血壓藥」這種句中用字
_TRAILING_PARTICLES = ("了", "啦", "喔", "唷", "哦", "呀", "呢", "耶", "吧", "嘛", "囉", "的")
_PUNCTUATION_PATTERN = re.compile(r"^[\s、,，.。!！?？~～\-—]+|[\s、,，.。!！?？~～\-—]+$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


class CanonicalError(ValueError):
    """canonical key 組成資料不完整或不合法。"""


@dataclass(frozen=True)
class ConceptPredicates:
    """單一 concept 的謂語受控詞彙。"""

    canonical: tuple[str, ...]
    aliases: dict[str, str]


@dataclass(frozen=True)
class PredicateLexicon:
    """server-owned 謂語與主體正規化詞彙。"""

    version: str
    other_token: str
    subject_aliases: dict[str, str]
    concepts: dict[str, ConceptPredicates]

    def candidates(self, concept_id: str) -> tuple[str, ...]:
        entry = self.concepts.get(concept_id)
        return entry.canonical if entry else ()

    def candidates_for_prompt(self, concept_ids: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        """組 prompt 用的候選表；沒有登記的 concept 回空 tuple。"""
        return {concept_id: self.candidates(concept_id) for concept_id in concept_ids}


@dataclass(frozen=True)
class PredicateResolution:
    """謂語正規化結果。"""

    value: str
    matched: bool
    matched_concept_id: str | None = None
    via_alias: bool = False
    via_fuzzy_embedding: bool = False
    similarity_score: float | None = None
    raw_predicate: str = ""


def load_predicate_lexicon(assets_dir: Path | str | None = None) -> PredicateLexicon:
    resolved = Path(assets_dir) if assets_dir is not None else TAXONOMY_ASSETS_DIR
    return _load_predicate_lexicon_cached(str(resolved.resolve()))


@lru_cache(maxsize=8)
def _load_predicate_lexicon_cached(assets_dir: str) -> PredicateLexicon:
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
    """檢查詞彙與分類體系是否一致；回傳問題清單，空清單代表通過。"""
    problems: list[str] = []
    for concept_id in lexicon.concepts:
        if taxonomy.get(concept_id) is None:
            problems.append(f"謂語詞彙指向不存在的節點：{concept_id}")
    for concept_id in taxonomy.leaf_ids():
        if concept_id not in lexicon.concepts:
            problems.append(f"葉節點缺少謂語候選：{concept_id}")
    return problems


def normalize_text(value: str) -> str:
    """字形正規化：NFKC、去空白、去頭尾標點與句尾語助詞。"""
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

    `extra_aliases` 供 runtime 疊加長者專屬別名（例如 `elders.family` 的稱謂與姓名），
    因為那些別名依長者而異，不適合寫死在資產裡。
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
    """收斂謂語。

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
    """依 `EVENT_SLOT_MINUTES` 計算固定邊界的時間桶標籤。

    `slot_index = floor(minute_of_day / slot_minutes)`；粒度為整小時的倍數時輸出 `SLOT_HH`，
    否則輸出 `SLOT_HHMM`（規範見 docs/framework.md）。
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
    """一般事件的 canonical key：`Date#Slot#Subject#Predicate`。"""
    subject_text = normalize_text(subject)
    predicate_text = normalize_text(predicate)
    if not subject_text or not predicate_text:
        raise CanonicalError("一般事件的 canonical key 需要 subject 與 predicate")
    for part in (subject_text, predicate_text):
        if KEY_SEPARATOR in part:
            raise CanonicalError(f"canonical key 組成不得包含分隔符號：{part}")
    return KEY_SEPARATOR.join((day_key(ts), slot_label(ts, slot_minutes), subject_text, predicate_text))


def routine_completion_key(routine_id: str, routine_date: str) -> str:
    """routine 完成事件的 canonical key。

    刻意不含 `routine_version`：同一天不同版本的 routine 完成仍是同一個 logical occurrence，
    手動完成與對話完成也要收斂到同一筆 event。
    """
    if not routine_id or not routine_date:
        raise CanonicalError("routine completion 需要 routine_id 與 routine_date")
    return KEY_SEPARATOR.join((ROUTINE_KEY_PREFIX, routine_id, routine_date))


def safety_episode_key(session_id: str, episode: str) -> str:
    """高風險 safety 事件的 canonical key：同 session 同 episode 的 realtime 與 batch 收斂。"""
    if not session_id or not episode:
        raise CanonicalError("safety event 需要 session_id 與 episode")
    episode_text = normalize_text(episode)
    return KEY_SEPARATOR.join((SAFETY_KEY_PREFIX, session_id, episode_text))


def event_id_for(elder_id: str, canonical_key: str) -> str:
    """由 `elder_id + canonical_event_key` 產生穩定 `event_id`。"""
    if not elder_id or not canonical_key:
        raise CanonicalError("event_id 需要 elder_id 與 canonical_event_key")
    signature = f"{elder_id}:{canonical_key}".encode("utf-8")
    digest = hashlib.sha256(signature).hexdigest()[:EVENT_ID_HASH_LENGTH]
    return f"{EVENT_ID_PREFIX}{digest}"


def event_time_key(ts: str, event_id: str) -> str:
    """GSI `events-by-time` 的排序鍵；`ts` 必須已正規化為固定毫秒精度。"""
    return f"{ts}{KEY_SEPARATOR}{event_id}"
