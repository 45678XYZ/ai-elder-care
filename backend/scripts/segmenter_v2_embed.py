"""步驟 3：抽 turn 級 embedding 並落地快取。

    python scripts/segmenter_v2_embed.py data/segmenter_v2/train_zh.jsonl data/segmenter_v2/dev_zh.jsonl

快取以 `sha256(model_id + text)` 為 key，重訓不再付費；不同 embedding 模型各自一份快取檔，
因為換模型就是換座標系，混用會讓特徵無意義。

成本量級：訓練集約 2.5 萬條 utterance。Cohere 一次可送 96 條（約 260 次呼叫）；
Titan 一次一條。中斷後重跑只補缺的部分。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.segmenter_v2 import corpora  # noqa: E402
from training.segmenter_v2.embeddings import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    CachedBedrockEmbedder,
    EmbeddingCache,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="抽取並快取 turn embedding")
    parser.add_argument("inputs", nargs="+", help="正規化後的對話 JSONL")
    parser.add_argument("--model", default="amazon.titan-embed-text-v2:0")
    parser.add_argument("--dim", type=int, default=1024)
    parser.add_argument("--batch", type=int, default=200, help="每批送多少條文字")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args(argv[1:])

    cache_name = f"{args.model.replace(':', '_').replace('/', '_')}.jsonl"
    cache = EmbeddingCache(Path(args.cache_dir) / cache_name, args.model, args.dim)
    embedder = CachedBedrockEmbedder(args.model, args.dim, cache=cache)

    texts: list[str] = []
    for path in args.inputs:
        for dialogue in corpora.load_dialogues(path):
            texts.extend(turn.text for turn in dialogue.turns)
    unique = list(dict.fromkeys(texts))

    missing = [text for text in unique if cache.get(text) is None]
    print(f"文字總數 {len(texts)}（去重後 {len(unique)}），快取已有 {len(unique) - len(missing)}")
    print(f"待抽取 {len(missing)} 條，模型 {args.model} / {args.dim} 維")

    for start in range(0, len(missing), args.batch):
        batch = missing[start : start + args.batch]
        embedder.embed_documents(batch)
        print(f"  進度 {min(start + args.batch, len(missing))}/{len(missing)}")

    print(f"完成；快取共 {len(cache)} 筆，本次 API 呼叫 {embedder.api_calls} 條文字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
