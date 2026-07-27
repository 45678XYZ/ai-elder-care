"""與執行期共用的契約：feature spec、特徵抽取、artifact 格式。

訓練時抽的特徵必須與 Lambda 推論時抽的完全相同，否則模型上線等於用錯座標系。舊版 pairwise
模型就是因為訓練與推論的特徵定義各走各的（加上綁死 MiniLM 的 384 維座標）而不能沿用。這裡
刻意「匯入執行期實作」而不是複製一份，讓漂移在語法層就不可能發生。

執行期實作：`src/extraction/segmenter.py`（同一個 backend 套件，不需要任何 sys.path 手術）。
"""

from src.extraction.chunker import Turn, depth_scores
from src.extraction.segmenter import (
    FEATURE_SPEC,
    SEGMENTER_ARTIFACT_FILE,
    PairwiseSegmenter,
    extract_features,
    load_segmenter,
)

# artifact 的格式版本；載入端會比對 feature_spec，這個欄位供人辨識訓練批次
ARTIFACT_VERSION = "pairwise-v2-1"

__all__ = [
    "ARTIFACT_VERSION",
    "FEATURE_SPEC",
    "SEGMENTER_ARTIFACT_FILE",
    "PairwiseSegmenter",
    "Turn",
    "depth_scores",
    "extract_features",
    "load_segmenter",
]
