"""由相對時間表達與謂語推出事件身分，供 Task 4 驗收與問題排查。

    python -m scripts.resolve_event_identity "昨天晚上吃了血壓藥" \
        --type medication \
        --predicate 吃血壓藥 --subject 阿嬤 \
        --reference 2026-07-26T09:41:23.456+08:00

參考時間必填（預設值只是為了方便手動試跑）；正式流程一律傳入來源 turn 的 `created_at`。
"""

import argparse
import sys

from src.extraction.canonical import (
    canonical_event_key,
    event_id_for,
    event_time_key,
    load_predicate_lexicon,
    normalize_predicate,
    normalize_subject,
    slot_label,
)
from src.extraction.temporal import day_key, resolve_observed_at
from src.extraction.taxonomy import load_taxonomy

DEFAULT_TYPE = "medication"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="推導 canonical 事件身分")
    parser.add_argument("expression", help="原始時間表達，如「昨天晚上」")
    parser.add_argument("--type", default=DEFAULT_TYPE, help="高階事件類別 (diet, medication, etc)")
    parser.add_argument("--predicate", default="吃血壓藥", help="模型輸出的謂語")
    parser.add_argument("--subject", default="我", help="模型輸出的事件主體")
    parser.add_argument("--elder-id", default="eld_a1b2c3d4e5f6")
    parser.add_argument("--reference", default="2026-07-26T09:41:23.456+08:00", help="來源 turn 的 created_at")
    parser.add_argument("--slot-minutes", type=int, default=30)
    args = parser.parse_args(argv[1:])

    taxonomy = load_taxonomy()
    lexicon = load_predicate_lexicon()

    ts = resolve_observed_at(None, args.expression, args.reference)
    subject = normalize_subject(args.subject, lexicon)
    predicate = normalize_predicate(args.predicate, lexicon)
    key = canonical_event_key(ts, subject, predicate.value, args.slot_minutes)
    event_id = event_id_for(args.elder_id, key)

    print(f"reference            : {args.reference}")
    print(f"expression           : {args.expression}")
    print(f"ts                   : {ts}")
    print(f"date / slot          : {day_key(ts)} / {slot_label(ts, args.slot_minutes)}")
    print(f"type                 : {args.type}")
    print(f"taxonomy_version     : {taxonomy.taxonomy_version}")
    print(f"subject              : {args.subject} -> {subject}")
    print(
        "predicate            : "
        f"{args.predicate} -> {predicate.value}"
        f"（matched={predicate.matched}）"
    )
    print(f"canonical_event_key  : {key}")
    print(f"event_id             : {event_id}")
    print(f"event_time_key       : {event_time_key(ts, event_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
