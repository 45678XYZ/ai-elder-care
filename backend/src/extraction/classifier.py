"""RAC 多標籤分類器。

移植自 aws-hackathon 的 `rac_classifier`（Multi-Shot 模式，Top-K=14），保留 HMLC
決策原則與候選標籤定義的 prompt 結構，換掉三件事：

- **client 換成 Bedrock structured outputs**。分類的 schema 形狀固定（只有候選 enum 會變），
  grammar 快取命中率高，而分類最需要的就是「不要回候選集以外的標籤」。
- **不索取原文片段**。上游會要 `evidence_span`，那是逐字稿摘錄；決策 D 明文不落地，
  連要都不要，追溯改用 `evidence_conversation_ids`。
- **輸出確定性排序**。剪枝與後續 canonical key 都依賴同一組輸入產生同一組輸出。

`rationale` 仍然索取：它是分類理由而非逐字稿，對品質與除錯有幫助，但只進 log 不進 events。
"""

from collections.abc import Sequence
import json
import logging

from src.shared import bedrock

from .models import CandidateConcept, ClassificationResult, LabelHit
from .taxonomy import Taxonomy

logger = logging.getLogger(__name__)

# prompt 與輸出契約的版本；寫進 metadata 供比對 A/B 與回溯
CLASSIFIER_VERSION = "rac-classifier-1"

SYSTEM_PROMPT = (
    "你是嚴謹的長者照護主題多標籤分類助手，只依據對話內容判斷，"
    "不補充對話沒有提到的事情，也不輸出候選清單以外的標籤。"
)


def build_classification_schema(candidate_ids: Sequence[str]) -> dict:
    """分類輸出的 JSON Schema。

    `concept_id` 以 enum 收斂到候選集，這是擋掉幻覺標籤最便宜的一層；
    形狀落在 Bedrock structured outputs 支援的子集內（`additionalProperties: false`、無數值約束）。
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
    """組 Multi-Shot 分類 prompt。"""
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
    """對單一 chunk 做多標籤分類。

    候選集為空時直接回無命中，不浪費一次模型呼叫。
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
    """由分類體系直接組候選（不經向量檢索）。

    供全量候選的離線比對與測試使用；正式路徑走 `retriever` 的 Top-K。
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
    """把分類結果壓成單行供 log；不含逐字稿。"""
    return json.dumps(
        {
            "chunk_id": result.chunk_id,
            "hits": [[hit.concept_id, round(hit.confidence, 3)] for hit in result.hits],
            "structured_output": result.metadata.get("structured_output"),
        },
        ensure_ascii=False,
    )
