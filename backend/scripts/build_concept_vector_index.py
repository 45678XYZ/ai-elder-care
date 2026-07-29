"""建立 UCO 概念向量索引（S3 Vectors）。

索引維度在建立時固定，所以索引名稱帶模型與維度；比較 Titan v2 與 Cohere v3 時各建一份
並存，切換只改 `CONCEPT_VECTOR_INDEX` 環境變數（決策 B）。

    # 只看要寫入什麼，不呼叫 AWS
    python -m scripts.build_concept_vector_index --dry-run

    # 實際建立索引並寫入向量
    python -m scripts.build_concept_vector_index \
        --bucket ai-elder-care-vectors \
        --model amazon.titan-embed-text-v2:0 --dim 1024

需要 `s3vectors:CreateIndex`、`s3vectors:PutVectors` 與 `bedrock:InvokeModel` 權限。
"""

import argparse
import sys

import boto3

from src.extraction.retriever import build_index_payload, load_concept_chunks, put_index_payload
from src.shared.bedrock import BedrockEmbeddingProvider


def default_index_name(model_id: str, dimension: int) -> str:
    """由模型與維度推導索引名稱，避免不同座標系的向量混進同一個索引。"""
    slug = model_id.split(":")[0].replace(".", "-").replace("_", "-").lower()
    return f"uco-concepts-{slug}-{dimension}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="建立概念向量索引")
    parser.add_argument("--bucket", default="", help="S3 Vectors vector bucket 名稱")
    parser.add_argument("--index", default="", help="索引名稱；預設由模型與維度推導")
    parser.add_argument("--model", default="amazon.titan-embed-text-v2:0")
    parser.add_argument("--dim", type=int, default=1024)
    parser.add_argument("--create-index", action="store_true", help="寫入前先建立索引")
    parser.add_argument("--dry-run", action="store_true", help="只列出將寫入的 sub-chunk")
    args = parser.parse_args(argv[1:])

    index_name = args.index or default_index_name(args.model, args.dim)
    chunks = load_concept_chunks()
    concepts = sorted({chunk.concept_id for chunk in chunks})

    print(f"index          : {index_name}")
    print(f"model / dim    : {args.model} / {args.dim}")
    print(f"sub-chunks     : {len(chunks)}")
    print(f"concepts       : {len(concepts)}")

    if args.dry_run:
        for chunk in chunks[:5]:
            print(f"  {chunk.chunk_id:<60} {chunk.aspect_type:<12} {chunk.embedding_text[:40]}…")
        print("（dry-run，未呼叫 AWS）")
        return 0

    if not args.bucket:
        parser.error("非 dry-run 時必須提供 --bucket")

    s3vectors = boto3.client("s3vectors")
    if args.create_index:
        # 距離度量固定 cosine，與檢索端「相似度 = 1 - distance」的換算對應
        s3vectors.create_index(
            vectorBucketName=args.bucket,
            indexName=index_name,
            dimension=args.dim,
            distanceMetric="cosine",
            dataType="float32",
        )
        print(f"已建立索引 {index_name}")

    embedder = BedrockEmbeddingProvider(args.model, args.dim)
    payload = build_index_payload(chunks, embedder)
    written = put_index_payload(s3vectors, args.bucket, index_name, payload)
    print(f"已寫入 {written} 筆向量")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
