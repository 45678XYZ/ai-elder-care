"""步驟 6：held-out 評測與上線 gate 判定。

    python scripts/segmenter_v2_evaluate.py \
        --test data/segmenter_v2/test_real_zh.jsonl \
        --artifact results/segmenter_v2/pairwise_v2.json

同時跑三個切分器並用同一組指標比較：

| 系統 | 意義 |
|---|---|
| `every_3_turns` | 機械切分基線。放它是為了證明模型不是退化——舊 TF-IDF 版看起來 90% 準確，實際只是每 3 輪切一次 |
| `embedding_depth` | 無監督基線，也是本專案不需要訓練就能上線的路徑 |
| `pairwise_v2` | 待判定的有監督模型 |

**gate：`pairwise_v2` 必須在人工標註的測試集上同時勝過兩個基線**（micro F1 更高、且 Pk 更低），
才可設為 `CHUNKER_TYPE` 預設；否則預設留在 `embedding_depth`。

指標同時看 P/R/F1 與 Pk／WindowDiff：切分任務對「差一格」敏感，只看 F1 會把差一格罰成
一個假陽性加一個假陰性，過度懲罰；Pk／WindowDiff 是切分文獻的標準補充。
另外回報容忍差一格的 F1，用來判斷模型是「抓錯位置」還是「完全沒抓到」。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import baselines, corpora, paths  # noqa: E402
from training.segmenter_v2.contract import extract_features  # noqa: E402
from training.segmenter_v2.embeddings import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    CachedBedrockEmbedder,
    EmbeddingCache,
)
from training.segmenter_v2.export import probability  # noqa: E402
from training.segmenter_v2.metrics import aggregate, evaluate_dialogue  # noqa: E402


def predict_pairwise(artifact, dialogue, vectors) -> list[int]:
    rows = extract_features(dialogue.turns, vectors)
    probabilities = [probability(artifact, row) for row in rows]
    return corpora.boundaries_from_gaps(probabilities, float(artifact["threshold"]))


def run_system(name, dialogues, vectors_by_id, predictor) -> dict:
    per_dialogue = []
    for dialogue in dialogues:
        if dialogue.turn_count < 2:
            continue
        predicted = predictor(dialogue, vectors_by_id[dialogue.dialogue_id])
        per_dialogue.append(evaluate_dialogue(dialogue.boundaries, predicted, dialogue.turn_count))
    return {"system": name, **aggregate(per_dialogue)}


def gate_decision(results: dict[str, dict]) -> dict:
    """gate：同時勝過兩個基線才算通過。"""
    candidate = results.get("pairwise_v2")
    if candidate is None:
        return {"passed": False, "reason": "未提供 pairwise_v2 artifact"}

    reasons = []
    for baseline_name in ("every_3_turns", "embedding_depth"):
        baseline = results.get(baseline_name)
        if baseline is None:
            reasons.append(f"缺少基線 {baseline_name}")
            continue
        if candidate["micro_f1"] <= baseline["micro_f1"]:
            reasons.append(
                f"micro_f1 未勝過 {baseline_name}（{candidate['micro_f1']} vs {baseline['micro_f1']}）"
            )
        if candidate["macro_pk"] >= baseline["macro_pk"]:
            reasons.append(
                f"Pk 未優於 {baseline_name}（{candidate['macro_pk']} vs {baseline['macro_pk']}）"
            )
    return {
        "passed": not reasons,
        "reason": "；".join(reasons) if reasons else "同時勝過兩個基線",
        "action": (
            "可把 CHUNKER_TYPE 預設改為 pairwise_v2，並把 artifact 複製到 "
            "backend/src/extraction/assets/segmenter/"
            if not reasons
            else "預設留在 embedding_depth，artifact 不上線"
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="評測分塊器並判定上線 gate")
    parser.add_argument("--test", required=True, help="人工標註的測試集（gate 主判定）")
    parser.add_argument("--artifact", default="", help="pairwise_v2.json；留空只跑基線")
    parser.add_argument("--model", default="amazon.titan-embed-text-v2:0")
    parser.add_argument("--dim", type=int, default=1024)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--depth-k", type=float, default=0.5)
    parser.add_argument("--baseline-n", type=int, default=3)
    parser.add_argument("--out", default=str(paths.RESULTS_DIR / "eval_report.json"))
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args(argv[1:])

    dialogues = corpora.load_dialogues(args.test)
    summary = corpora.describe(dialogues)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if corpora.LABEL_HUMAN not in summary["label_sources"]:
        print("! 警告：測試集不含真人標註，gate 判定不成立（合成標籤只能當 smoke test）")

    cache_name = f"{args.model.replace(':', '_').replace('/', '_')}.jsonl"
    cache = EmbeddingCache(Path(args.cache_dir) / cache_name, args.model, args.dim)
    embedder = CachedBedrockEmbedder(args.model, args.dim, cache=cache, strict=not args.allow_api)

    vectors_by_id = {
        dialogue.dialogue_id: embedder.embed_documents([turn.text for turn in dialogue.turns])
        for dialogue in dialogues
    }

    results: dict[str, dict] = {}
    results["every_3_turns"] = run_system(
        "every_3_turns",
        dialogues,
        vectors_by_id,
        lambda dialogue, _: baselines.every_n_turns(dialogue.turn_count, args.baseline_n),
    )
    results["embedding_depth"] = run_system(
        "embedding_depth",
        dialogues,
        vectors_by_id,
        lambda _, vectors: baselines.embedding_depth(vectors, depth_k=args.depth_k),
    )

    if args.artifact:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        if artifact["embedding_dim"] != args.dim or artifact["embedding_model_id"] != args.model:
            raise SystemExit(
                "artifact 的 embedding 模型／維度與評測設定不符；換模型必須重訓，不可硬跑"
            )
        results["pairwise_v2"] = run_system(
            "pairwise_v2",
            dialogues,
            vectors_by_id,
            lambda dialogue, vectors: predict_pairwise(artifact, dialogue, vectors),
        )

    print()
    header = f"{'system':<18}{'micro_f1':>10}{'F1(±1)':>10}{'Pk':>8}{'WinDiff':>10}"
    print(header)
    for name, result in results.items():
        print(
            f"{name:<18}{result['micro_f1']:>10}{result['macro_f1_tolerance_1']:>10}"
            f"{result['macro_pk']:>8}{result['macro_window_diff']:>10}"
        )

    gate = gate_decision(results)
    print(f"\ngate：{'通過' if gate['passed'] else '未通過'} — {gate['reason']}")
    print(f"處置：{gate['action']}")

    report = {"test_set": summary, "results": results, "gate": gate}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"報告已寫入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
