"""把 backend/src 整理成 Lambda 部署包內容。

Terraform 的 `archive_file` 只能壓縮既有目錄，而 `backend/` 底下有 `.venv`、`tests`、
`__pycache__` 等不該進部署包的東西。這支腳本先把要發佈的檔案複製到
`terraform/build/backend/`，Terraform 再壓縮那個乾淨的目錄。

    python -m scripts.package_lambda

輸出結構（zip 根目錄）：

    src/handlers/...        API 與事件入口
    src/shared/...          共用層
    src/extraction/...      萃取 pipeline
    src/extraction/assets/  分類體系、檢索與分塊資產（必須隨包發佈）

handler 字串因此是 `src.handlers.batch_extractor.handler`。
"""

import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BACKEND_DIR.parent / "terraform" / "build" / "backend"

# 執行期不需要的東西；資產目錄刻意不排除，pipeline 靠它才能跑
EXCLUDED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".venv", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def package(output_dir: Path = DEFAULT_OUTPUT) -> tuple[int, int]:
    """複製 `src/` 到輸出目錄，回傳 `(檔案數, 位元組數)`。"""
    source_root = BACKEND_DIR / "src"
    target_root = output_dir / "src"
    if output_dir.exists():
        # 每次重建，避免上一版刪掉的模組留在包裡
        shutil.rmtree(output_dir)
    target_root.parent.mkdir(parents=True, exist_ok=True)

    file_count = 0
    total_bytes = 0
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        if should_skip(relative):
            continue
        destination = target_root / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        file_count += 1
        total_bytes += path.stat().st_size
    return file_count, total_bytes


def main(argv: list[str]) -> int:
    output_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    file_count, total_bytes = package(output_dir)
    print(f"輸出目錄：{output_dir}")
    print(f"檔案數  ：{file_count}")
    print(f"總大小  ：{total_bytes / 1024:.1f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
