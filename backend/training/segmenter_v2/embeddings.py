"""Bedrock embedding 與磁碟快取。

訓練語料約兩萬條 utterance，重訓時沒必要重新付費也不該重新等，因此以 `sha256(model + text)`
為 key 落地快取。快取檔同時記錄模型與維度：換模型就是換座標系，混用會讓特徵無意義，
所以不同模型各自一份快取檔。
"""

from collections.abc import Iterable, Sequence
from pathlib import Path
import hashlib
import json
import logging

from training.segmenter_v2.paths import EMBEDDING_CACHE_DIR

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = EMBEDDING_CACHE_DIR


def cache_key(model_id: str, text: str) -> str:
    return hashlib.sha256(f"{model_id}\u0000{text}".encode("utf-8")).hexdigest()


class EmbeddingCache:
    """以 JSONL 落地的向量快取。

    用 JSONL 而不是 .npy：向量是稀疏地增量累積的（翻譯批次會分次跑），append-only 格式
    在中斷後仍可用，重跑只補缺的部分。
    """

    def __init__(self, path: Path | str, model_id: str, dimension: int):
        self.path = Path(path)
        self.model_id = model_id
        self.dimension = dimension
        self._vectors: dict[str, list[float]] = {}
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if item.get("model_id") != self.model_id:
                    # 不同模型的向量不可混用
                    continue
                self._vectors[item["key"]] = item["vector"]
        logger.info("載入 embedding 快取：%s 筆", len(self._vectors))

    def __len__(self) -> int:
        return len(self._vectors)

    def get(self, text: str) -> list[float] | None:
        return self._vectors.get(cache_key(self.model_id, text))

    def put_many(self, pairs: Iterable[tuple[str, list[float]]]) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self.path.open("a", encoding="utf-8") as fp:
            for text, vector in pairs:
                key = cache_key(self.model_id, text)
                if key in self._vectors:
                    continue
                self._vectors[key] = vector
                fp.write(
                    json.dumps(
                        {
                            "key": key,
                            "model_id": self.model_id,
                            "dimension": self.dimension,
                            "vector": vector,
                        }
                    )
                    + "\n"
                )
                written += 1
        return written


class CachedBedrockEmbedder:
    """對外行為與執行期的 `EmbeddingProvider` 一致，但先查快取。

    `strict` 為 True 時遇到快取未命中直接報錯，用在「不該再呼叫 API」的情境
    （例如評測腳本要求所有向量都已預先抽好）。
    """

    def __init__(
        self,
        model_id: str,
        dimension: int,
        *,
        cache: EmbeddingCache | None = None,
        client=None,
        strict: bool = False,
    ):
        self.model_id = model_id
        self.dimension = dimension
        self.cache = cache or EmbeddingCache(
            DEFAULT_CACHE_DIR / f"{model_id.replace(':', '_').replace('/', '_')}.jsonl",
            model_id,
            dimension,
        )
        self.strict = strict
        self._client = client
        self._provider = None
        self.api_calls = 0

    @property
    def provider(self):
        if self._provider is None:
            # 延後匯入：純快取模式（strict）不需要 boto3 憑證
            from src.shared.bedrock import BedrockEmbeddingProvider

            self._provider = BedrockEmbeddingProvider(
                self.model_id, self.dimension, client=self._client
            )
        return self._provider

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        missing = [text for text in texts if self.cache.get(text) is None]
        unique_missing = list(dict.fromkeys(missing))
        if unique_missing:
            if self.strict:
                raise RuntimeError(
                    f"embedding 快取缺 {len(unique_missing)} 筆；請先跑 segmenter_v2_embed.py"
                )
            vectors = self.provider.embed_documents(unique_missing)
            self.api_calls += len(unique_missing)
            self.cache.put_many(zip(unique_missing, vectors))
        return [self.cache.get(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def stub_vector(text: str, dimension: int) -> list[float]:
    """確定性的假向量。

    只用於離線煙霧測試：讓整條訓練／評測流程在沒有 AWS 憑證的情況下也能跑通，
    驗證的是「程式接得起來」，不是模型品質。品質數字一律要用真實 embedding 重跑。
    """
    buckets = [0.0] * dimension
    for index, char in enumerate(text):
        buckets[(ord(char) + index) % dimension] += 1.0
    norm = sum(value * value for value in buckets) ** 0.5 or 1.0
    return [value / norm for value in buckets]
