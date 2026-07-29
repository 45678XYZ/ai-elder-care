"""離線工作流的檔案位置。

全部由 repo 根目錄推導，不依賴當下的工作目錄——訓練腳本常從 `backend/` 執行，語料與結果
卻應該落在 repo 的 `data/`，用相對路徑會依 cwd 產生兩份不同位置的資料。

第三方語料（Def-DTS、SeniorTalk 衍生語料）**不複製進本 repo**：DialSeg711／TIAGE 隨
Def-DTS 散布，SeniorTalk 是 CC BY-NC-SA 4.0。預設指向 `aws-hackathon` 既有副本，可用環境變數
覆寫成任何本機路徑。
"""

from pathlib import Path
import os

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# 工作流產出：語料、embedding 快取、標註檔、訓練與評測結果（皆 gitignore）
WORK_DIR = Path(os.environ.get("SEGMENTER_V2_WORK_DIR") or REPO_ROOT / "data" / "segmenter_v2")
CORPORA_DIR = WORK_DIR
EMBEDDING_CACHE_DIR = WORK_DIR / "embedding_cache"
ANNOTATION_DIR = WORK_DIR / "annotation"
RESULTS_DIR = Path(os.environ.get("SEGMENTER_V2_RESULTS_DIR") or WORK_DIR / "results")

# 執行期 artifact 的目的地；gate 通過後才複製進去
RUNTIME_ASSET_DIR = BACKEND_DIR / "src" / "extraction" / "assets" / "segmenter"

# 第三方語料來源
_DEFAULT_UPSTREAM = REPO_ROOT.parent / "aws-hackathon"
UPSTREAM_ROOT = Path(os.environ.get("SEGMENTER_V2_UPSTREAM_ROOT") or _DEFAULT_UPSTREAM)
DTS_SESSION_DIR = Path(
    os.environ.get("DTS_SESSION_DATASETS") or UPSTREAM_ROOT / "Def-DTS" / "data" / "DTS_session_datasets"
)
UPSTREAM_DATA_DIR = Path(os.environ.get("SEGMENTER_V2_UPSTREAM_DATA") or UPSTREAM_ROOT / "data")


def describe() -> str:
    """給腳本開頭印出來用；路徑錯了要一眼看得出來，而不是等到讀檔失敗。"""
    return "\n".join(
        [
            f"repo root        : {REPO_ROOT}",
            f"work dir         : {WORK_DIR}",
            f"results dir      : {RESULTS_DIR}",
            f"runtime assets   : {RUNTIME_ASSET_DIR}",
            f"Def-DTS datasets : {DTS_SESSION_DIR}",
            f"upstream corpora : {UPSTREAM_DATA_DIR}",
        ]
    )
