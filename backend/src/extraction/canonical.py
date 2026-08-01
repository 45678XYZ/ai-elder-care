"""Canonical 事件身分、受控詞彙與 `event_id` 生成器模組。

提供事件身分規範化、謂語/主體受控詞彙映射、時間桶 (Slot) 計算與確定性 `event_id` 產出。
架構規範與寫入規則詳見 `docs/framework.md` 的「Canonical identity 與寫入規則」。

本模組設計目的與核心機制：
- **伺服器端受控詞彙 (Server-Owned Controlled Lexicon)**：主體與謂語在組裝鍵值前必須經由伺服器端正規化。例如將「阿公」、「長者本人」統一收斂為「長者」，將「吃了降壓藥」、「服血壓藥」透過 `predicate_lexicon.json` 映射至相同的受控謂語，防止同一照護事實因口語表達差異而重複寫入。
- **三類事件 Canonical Key 分立**：
  1. 一般事件：`Date#Slot#Subject#Predicate`
  2. Routine 完成：`routine_completion#routine_id#routine_date`（刻意排除 `routine_version`，同日改版手動或對話完成均收斂至同一筆）
  3. 高風險 Safety：`SAFETY#alert_id`（收斂同一警報情節的 emergency → escalation → mitigation）
- **穩定確定性 `event_id` 產出**：由 `elder_id + canonical_event_key` 進行 SHA-256 雜湊產生。該識別碼與 Chunk 拆分方式、模型版本完全無關，能保證 SQS 批次作業 Retry 或 DLQ Replay 時為具備冪等性的覆蓋/去重寫入。
"""

from collections.abc import Mapping, Sequence

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import hashlib
import json
import logging
import re
import unicodedata

TAXONOMY_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "taxonomy"
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
class PredicateLexicon:
    """伺服器管轄之主體與謂語受控詞彙庫模型。"""

    version: str
    other_token: str
    subject_aliases: dict[str, str]


@dataclass(frozen=True)
class PredicateResolution:
    """謂語正規化結果容器。"""

    value: str
    matched: bool
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

    return PredicateLexicon(
        version=raw.get("version") or "",
        other_token=raw.get("other_token") or "__other__",
        subject_aliases=dict(raw.get("subject_aliases") or {}),
    )




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


def build_family_aliases(family: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    """將 `elders.family` (`[{"relation": "女兒", "name": "小芳", "note": "大女兒"}]`) 轉譯為別名映射表。"""
    if not family:
        return {}
    aliases: dict[str, str] = {}
    for member in family:
        relation = member.get("relation")
        if not relation:
            continue
        name = member.get("name")
        if name:
            aliases[name] = relation
        note = member.get("note")
        if note:
            aliases[note] = relation
    return aliases


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
    predicate: str | None,
    lexicon: PredicateLexicon,
    embedder: Any | None = None,
) -> PredicateResolution:
    """將口語謂語正規化為穩定的文字表述。

    採用開放世界策略 (Open-World Predicate)：
    - 不再強制將 predicate 收斂至受控詞彙 (canonical list)
    - LLM 自由撰寫精簡動作短語，本函式僅進行文字正規化 (NFKC、去尾綴、去標點)
    - 謂語的同義收斂由 dedup.py 的 embedding cosine similarity 在去重階段處理
    - 此設計消除了「predicate_lexicon canonical 清單太窄導致 LLM 被迫亂選」的根因

    保留 lexicon/embedder 參數以維持呼叫端向後相容。
    """
    raw_text = predicate or ""
    text = normalize_text(raw_text)
    if not text:
        return PredicateResolution(value=text, matched=False, raw_predicate=raw_text)

    # 開放世界策略：直接使用正規化後的文字，標記為 matched=True（不再區分 canonical/alias/fuzzy）
    return PredicateResolution(
        value=text,
        matched=True,
        raw_predicate=raw_text,
    )



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


def safety_alert_key(alert_id: str) -> str:
    """組合高風險 Safety 事件的唯一身分鍵：`SAFETY#alert_id`。

    `alert_id` 由 Bedrock Agent tool calling（`notify_caregiver`）在首次 `emergency` 時產生，
    並回傳給 Agent 以便後續 `critical_escalation`／`mitigation` 帶入同一 `alert_id`，
    讓同一警報情節的 emergency → escalation → mitigation 收斂到同一筆 event。
    Batch 未來做 safety enrichment 時，可依 `evidence_conversation_ids` 或 `type=safety`
    查詢既有事件，再以相同 canonical key 做 revision enrichment。
    """
    if not alert_id:
        raise CanonicalError("safety event 需要 alert_id")
    alert_text = normalize_text(alert_id)
    return KEY_SEPARATOR.join((SAFETY_KEY_PREFIX, alert_text))


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
