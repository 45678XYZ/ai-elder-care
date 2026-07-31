"""步驟 4：訓練 pairwise_v2 並導出純 Python artifact。

    python scripts/segmenter_v2_train.py \
        --train data/segmenter_v2/train_zh.jsonl \
        --dev data/segmenter_v2/dev_zh.jsonl \
        --model amazon.titan-embed-text-v2:0 --dim 1024 \
        --out results/segmenter_v2

三個刻意的設計：

1. **交叉驗證按 dialogue_id 分組（GroupKFold）**。原版用 `StratifiedKFold` 對 pair 層級切分，
   同一段對話的相鄰 pair 會同時落在 train 與 val，而相鄰 pair 共用同一個 utterance——那是
   資料洩漏，指標本身就被高估。
2. **門檻在開發集上選，不在訓練集上選**。門檻直接決定切多切少，用訓練集選等於再洩漏一次。
3. **特徵來自執行期實作**（見 `training/segmenter_v2/contract.py`），確保訓練與 Lambda 推論
   抽的是同一組特徵；導出時還會比對 sklearn 分數，不一致就拒絕輸出。

輸出：`pairwise_v2.json`（artifact + model card）與 `train_report.json`。
artifact 要生效必須先通過 `segmenter_v2_evaluate.py` 的上線 gate，再複製到
`src/extraction/assets/segmenter/`。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import corpora, paths  # noqa: E402
from training.segmenter_v2.contract import ARTIFACT_VERSION, FEATURE_SPEC, extract_features  # noqa: E402
from training.segmenter_v2.embeddings import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    CachedBedrockEmbedder,
    EmbeddingCache,
)
from training.segmenter_v2.export import export_gradient_boosting  # noqa: E402
from training.segmenter_v2.metrics import aggregate, evaluate_dialogue  # noqa: E402

# 門檻掃描範圍；步長 0.05 已足夠，再細只是過擬合開發集
THRESHOLD_GRID = [round(0.05 * step, 2) for step in range(2, 19)]


def build_dataset(dialogues, embedder):
    """回傳 `(features, labels, groups, per_dialogue_index)`。"""
    features: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    spans: list[tuple[str, int, int]] = []

    for dialogue in dialogues:
        if dialogue.turn_count < 2:
            continue
        vectors = embedder.embed_documents([turn.text for turn in dialogue.turns])
        rows = extract_features(dialogue.turns, vectors)
        gaps = corpora.gap_labels(dialogue)
        start = len(features)
        features.extend(rows)
        labels.extend(gaps)
        groups.extend([dialogue.dialogue_id] * len(rows))
        spans.append((dialogue.dialogue_id, start, start + len(rows)))
    return features, labels, groups, spans


def cross_validate(features, labels, groups, *, folds: int, seed: int):
    """GroupKFold（by dialogue_id）的 held-out 分數。"""
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import GroupKFold

    x = np.asarray(features, dtype=float)
    y = np.asarray(labels, dtype=int)
    group_array = np.asarray(groups)

    unique_groups = len(set(groups))
    effective_folds = min(folds, unique_groups)
    splitter = GroupKFold(n_splits=effective_folds)

    fold_reports = []
    for index, (train_index, test_index) in enumerate(splitter.split(x, y, group_array)):
        model = GradientBoostingClassifier(random_state=seed)
        model.fit(x[train_index], y[train_index])
        probabilities = model.predict_proba(x[test_index])[:, 1]
        predictions = (probabilities >= 0.5).astype(int)

        true_positive = int(((predictions == 1) & (y[test_index] == 1)).sum())
        predicted_positive = int((predictions == 1).sum())
        actual_positive = int((y[test_index] == 1).sum())
        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / actual_positive if actual_positive else 0.0
        fold_reports.append(
            {
                "fold": index,
                "train_gaps": int(len(train_index)),
                "test_gaps": int(len(test_index)),
                "gap_precision": round(precision, 4),
                "gap_recall": round(recall, 4),
                "gap_f1": round(
                    2 * precision * recall / (precision + recall) if (precision + recall) else 0.0, 4
                ),
            }
        )
    return effective_folds, fold_reports


def pick_threshold(model, dialogues, embedder):
    """在開發集上挑邊界 F1 最高的門檻。"""
    import numpy as np

    scored = []
    for dialogue in dialogues:
        if dialogue.turn_count < 2:
            continue
        vectors = embedder.embed_documents([turn.text for turn in dialogue.turns])
        rows = extract_features(dialogue.turns, vectors)
        probabilities = model.predict_proba(np.asarray(rows, dtype=float))[:, 1]
        scored.append((dialogue, probabilities))

    results = []
    for threshold in THRESHOLD_GRID:
        per_dialogue = [
            evaluate_dialogue(
                dialogue.boundaries,
                corpora.boundaries_from_gaps(probabilities, threshold),
                dialogue.turn_count,
            )
            for dialogue, probabilities in scored
        ]
        summary = aggregate(per_dialogue)
        results.append({"threshold": threshold, **summary})

    best = max(results, key=lambda item: (item.get("micro_f1", 0.0), -item.get("macro_pk", 1.0)))
    return best, results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="訓練 pairwise_v2 分塊模型")
    parser.add_argument("--train", required=True)
    parser.add_argument("--dev", required=True)
    parser.add_argument("--model", default="amazon.titan-embed-text-v2:0")
    parser.add_argument("--dim", type=int, default=1024)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--out", default=str(paths.RESULTS_DIR))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument(
        "--allow-api",
        action="store_true",
        help="允許在訓練途中補抽 embedding；預設要求先跑 segmenter_v2_embed.py",
    )
    args = parser.parse_args(argv[1:])

    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier

    cache_name = f"{args.model.replace(':', '_').replace('/', '_')}.jsonl"
    cache = EmbeddingCache(Path(args.cache_dir) / cache_name, args.model, args.dim)
    embedder = CachedBedrockEmbedder(
        args.model, args.dim, cache=cache, strict=not args.allow_api
    )

    train_dialogues = corpora.load_dialogues(args.train)
    dev_dialogues = corpora.load_dialogues(args.dev)

    train_ids = {dialogue.dialogue_id for dialogue in train_dialogues}
    overlap = [d.dialogue_id for d in dev_dialogues if d.dialogue_id in train_ids]
    if overlap:
        raise SystemExit(f"訓練集與開發集有 {len(overlap)} 段重疊；切分必須 by dialogue_id")

    print(f"訓練集 {len(train_dialogues)} 段 / 開發集 {len(dev_dialogues)} 段")
    features, labels, groups, _ = build_dataset(train_dialogues, embedder)
    print(f"特徵 {len(features)} 筆 × {len(FEATURE_SPEC)} 維，正例率 {sum(labels) / len(labels):.4f}")

    effective_folds, fold_reports = cross_validate(
        features, labels, groups, folds=args.folds, seed=args.seed
    )
    print(f"GroupKFold（by dialogue_id）{effective_folds} 折：")
    for report in fold_reports:
        print(f"  fold {report['fold']}: F1={report['gap_f1']}")

    model = GradientBoostingClassifier(random_state=args.seed)
    model.fit(np.asarray(features, dtype=float), np.asarray(labels, dtype=int))

    best, threshold_scan = pick_threshold(model, dev_dialogues, embedder)
    print(f"開發集最佳門檻 {best['threshold']}：micro_f1={best['micro_f1']} Pk={best['macro_pk']}")

    model_card = {
        "artifact_version": ARTIFACT_VERSION,
        "trained_at": None,
        "embedding_model_id": args.model,
        "embedding_dim": args.dim,
        "feature_spec": list(FEATURE_SPEC),
        # 標籤與文本來源分兩行寫：標籤是真人標的，只有文字被機器翻譯
        "labels": "human-annotated (TIAGE / DialSeg711)",
        "text": sorted({dialogue.text_source for dialogue in train_dialogues}),
        "split": "GroupKFold by dialogue_id",
        "train_corpus": corpora.describe(train_dialogues),
        "dev_corpus": corpora.describe(dev_dialogues),
        "cross_validation": fold_reports,
        "dev_threshold_scan": threshold_scan,
        "selected_threshold": best["threshold"],
        "dev_metrics": {key: value for key, value in best.items() if key != "threshold"},
        "min_turns": 0,
        "gate": (
            "未經 segmenter_v2_evaluate.py 在人工標註的繁中測試集上同時勝過 "
            "embedding_depth 與 every-3-turns 兩個基線之前，不得設為 CHUNKER_TYPE 預設"
        ),
        "limitations": [
            "翻譯解決語言差，不解決領域差：訓練語料是英文任務型對話，不是台灣長者閒聊或 ASR 逐字稿",
            "機翻文本有 translationese，詞彙與句法分布偏離自然口語；長度類特徵已做對話內正規化以降低影響",
            "artifact 綁定訓練時的 embedding 座標系，換 embedding 模型必須重訓",
        ],
    }

    from datetime import datetime, timedelta, timezone

    model_card["trained_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()

    artifact = export_gradient_boosting(
        model,
        features,
        feature_spec=FEATURE_SPEC,
        embedding_model_id=args.model,
        embedding_dim=args.dim,
        threshold=best["threshold"],
        artifact_version=ARTIFACT_VERSION,
        model_card=model_card,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pairwise_v2.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "train_report.json").write_text(
        json.dumps(
            {"cross_validation": fold_reports, "dev_threshold_scan": threshold_scan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nartifact 已寫入 {out_dir / 'pairwise_v2.json'}（{len(artifact['trees'])} 棵樹）")
    print(f"下一步：跑 segmenter_v2_evaluate.py 判定上線 gate，通過後才複製到 {paths.RUNTIME_ASSET_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
