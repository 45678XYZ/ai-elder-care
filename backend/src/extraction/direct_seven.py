"""direct_seven pipeline：不分塊、不檢索、對整個 session 一次（或依字元上限分批）抽出七大類事件。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .canonical import PredicateLexicon, build_family_aliases
from .chunker import Turn
from .config import ExtractionConfig
from .results import LlmUsage, PipelineResult
from .shared_tail import EventOrigin, SharedTail, SuspectedRoutineLookup
from .seven_type import extract_seven_type_events, plan_turn_batches
from .taxonomy import Taxonomy


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
        tail = SharedTail(
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
