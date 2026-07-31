"""RAC (Retrieval-Augmented Classification) 多標籤分類器模組。

提供對話片段 (chunk) 在 Top-K 候選概念集上的多標籤識別。
架構規範與設計決策詳見 `docs/framework.md` 與 `docs/feature_events-extraction.md`。

本模組設計目的：
- 採用 **Bedrock Structured Outputs (`converse_json`)** 進行強約束解碼。JSON Schema 將 `concept_id` 的 `enum` 限制在候選集範圍內，以極低成本在 API 網關層徹底杜絕幻覺標籤。
- 遵循 **PII 最小化原則（決策 D）**：不索取原文片段 (evidence_span)，僅保留繁體中文判斷理由 (rationale) 供 CloudWatch 日誌觀察，資料落地追溯改用 `evidence_conversation_ids`。
- 確保 **輸出結果的確定性（Deterministic Sorting）**：命中的標籤按信心值遞減與 concept_id 字典序排序，保障下游 HMLC 剪枝與 Canonical Key 計算具重複再現性。
"""

from collections.abc import Sequence
import json
import logging

from src.shared import bedrock

from .models import CandidateConcept, ClassificationResult, LabelHit
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# 分類器 Prompt 與輸出契約版本；寫入結果 metadata 以利跨版本 A/B 比對與問題追溯
CLASSIFIER_VERSION = "rac-classifier-1"

# 系統 Prompt：強制要求模型僅依據對話判斷，嚴禁補充推測與輸出候選集以外的標籤
SYSTEM_PROMPT = (
    "你是嚴謹的長者照護主題多標籤分類助手，只依據對話內容判斷，"
    "不補充對話沒有提到的事情，也不輸出候選清單以外的標籤。"
)


def build_classification_schema(candidate_ids: Sequence[str]) -> dict:
    """生成 Bedrock Structured Outputs 所需的 JSON Schema。

    透過在 `concept_id` 欄位硬編碼 `enum: list(candidate_ids)`，能在語法解碼層層面
    直接封鎖模型產生候選清單外的標籤。Schema 結構嚴格遵守 Bedrock 支援子集
    （`additionalProperties: False`，不包含不支援的長度或範圍約束）。
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chunk_id": {"type": "string", "description": "必須與輸入的 chunk_id 一致"},
            "identified_labels": {
                "type": "array",
                "description": "命中的細分類節點；無命中則為空陣列",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "concept_id": {"type": "string", "enum": list(candidate_ids)},
                        "confidence": {"type": "number", "description": "判定信心值 0.0–1.0"},
                    },
                    "required": ["concept_id", "confidence"],
                },
            },
            "rationale": {"type": "string", "description": "分類理由（繁體中文，勿抄寫逐字稿）"},
        },
        "required": ["chunk_id", "identified_labels", "rationale"],
    }


def build_classification_prompt(
    chunk_id: str,
    transcript: str,
    candidates: Sequence[CandidateConcept],
) -> str:
    """組裝帶有候選概念詳細定義與典型情境的提示詞 (Multi-Shot Prompt)。

    逐一列出 Top-K 候選節點的定義、典型情境與同義詞，為 LLM 提供明確的排他分類邊界，
    避免將僅概念相似但細節不足的對話誤判為具體葉節點。
    """
    blocks: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        synonyms = "、".join(candidate.synonyms) if candidate.synonyms else "無"
        blocks.append(
            f"### {index}. {candidate.concept_id}（{candidate.display_name}）\n"
            f"- 定義：{candidate.definition or '無'}\n"
            f"- 典型情境：{candidate.retrieval_description or '無'}\n"
            f"- 同義詞：{synonyms}"
        )
    candidates_block = "\n\n".join(blocks)

    return f"""請對下列對話塊做 Top-Down 階層多標籤識別。

【分類原則】
1. 只能從下面 {len(candidates)} 個候選節點中選，不得自創或改寫 concept_id。
2. 候選同時包含具體的葉節點與較籠統的類別節點：
   - 對話講到明確細節時，選具體的葉節點。
   - 只提到主題但細節不足以歸到特定葉節點時，選對應的類別節點作為退守。
3. 每個命中都要給 0.0–1.0 的 confidence，並嚴格依各節點定義做排他判斷。
4. 對話沒有提到的主題不要標；沒有任何命中就回空陣列。
5. `rationale` 用繁體中文說明判斷依據，不要抄寫對話原文。
6. `chunk_id` 必須填 "{chunk_id}"。

【候選節點定義】
{candidates_block}

【待分類對話塊】
chunk_id："{chunk_id}"
對話內容（含說話角色）：
{transcript}
"""


def classify_chunk(
    chunk_id: str,
    transcript: str,
    candidates: Sequence[CandidateConcept],
    *,
    taxonomy: Taxonomy | None = None,
    min_confidence: float = 0.3,
    model_id: str | None = None,
    client=None,
) -> ClassificationResult:
    """執行對話片段的多標籤分類識別。

    當 `candidates` 為空時直接返回空命中，避免發起無效的 LLM API 呼叫；
    若降級路徑（無 Grammar 強制約束）回傳了候選集以外的標籤，或信心值低於門檻 (`min_confidence`)，
    會於後處理階段過濾，並將命中標籤依得分與 ID 確定性排序。
    """
    if not candidates:
        logger.warning("候選節點為空，跳過分類：chunk_id=%s", chunk_id)
        return ClassificationResult(
            chunk_id=chunk_id,
            hits=(),
            metadata={"classifier_version": CLASSIFIER_VERSION, "skipped": "no_candidates"},
        )

    candidate_ids = [candidate.concept_id for candidate in candidates]
    display_names = {candidate.concept_id: candidate.display_name for candidate in candidates}
    schema = build_classification_schema(candidate_ids)
    prompt = build_classification_prompt(chunk_id, transcript, candidates)

    data, metadata = bedrock.converse_json(
        prompt,
        schema,
        system=SYSTEM_PROMPT,
        model_id=model_id,
        schema_name="UCOClassificationOutput",
        client=client,
    )

    allowed = set(candidate_ids)
    hits: dict[str, LabelHit] = {}
    for raw in data.get("identified_labels") or []:
        if not isinstance(raw, dict):
            continue
        concept_id = raw.get("concept_id")
        if not concept_id:
            continue
        if concept_id not in allowed:
            # 走降級路徑（無 grammar 硬約束）時模型仍可能回傳候選集外但合法的 Taxonomy 標籤
            if taxonomy is not None and taxonomy.get(str(concept_id)) is not None:
                logger.info("分類回傳候選集外但存在於 Taxonomy 的合法節點：concept_id=%s", concept_id)
                node = taxonomy.get(str(concept_id))
                display_names[str(concept_id)] = node.label_zh if node else str(concept_id)
            else:
                logger.warning("分類回傳候選集外的無效節點，已丟棄：concept_id=%s", concept_id)
                continue
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            logger.warning("分類 confidence 非數值，已丟棄：concept_id=%s", concept_id)
            continue
        confidence = min(max(confidence, 0.0), 1.0)
        if confidence < min_confidence:
            continue
        existing = hits.get(concept_id)
        if existing is None or confidence > existing.confidence:
            hits[concept_id] = LabelHit(
                concept_id=concept_id,
                display_name=display_names.get(concept_id, ""),
                confidence=confidence,
            )

    # 進行確定性排序（信心值降序，同分時按字典序），保證下游剪枝與 canonical key 的再現性
    ordered = tuple(sorted(hits.values(), key=lambda hit: (-hit.confidence, hit.concept_id)))
    return ClassificationResult(
        chunk_id=chunk_id,
        hits=ordered,
        rationale=str(data.get("rationale") or ""),
        metadata={
            **metadata,
            "classifier_version": CLASSIFIER_VERSION,
            "candidate_count": len(candidates),
        },
    )


def candidates_from_taxonomy(taxonomy, concept_ids: Sequence[str]) -> tuple[CandidateConcept, ...]:
    """從分類體系直接建立候選概念清單（不經由向量檢索）。

    主要用於單元測試或離線對照實驗（全量候選情境），線上生產環境走 `retriever.py` 的 Top-K 檢索。
    """
    candidates = []
    for concept_id in concept_ids:
        node = taxonomy.get(concept_id)
        if node is None:
            continue
        candidates.append(
            CandidateConcept(
                concept_id=node.concept_id,
                display_name=node.display_name,
                definition=node.definition,
                retrieval_description=node.retrieval_description,
                synonyms=node.synonyms,
            )
        )
    return tuple(candidates)


def summarize_for_log(result: ClassificationResult) -> str:
    """將分類結果壓縮為單行 JSON 供結構化 Log 輸出。

    僅輸出概念 ID 與信心值，絕不包含對話原文逐字稿，符合 PII 最小化規範。
    """
    return json.dumps(
        {
            "chunk_id": result.chunk_id,
            "hits": [[hit.concept_id, round(hit.confidence, 3)] for hit in result.hits],
            "structured_output": result.metadata.get("structured_output"),
        },
        ensure_ascii=False,
    )

