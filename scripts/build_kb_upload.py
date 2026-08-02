#!/usr/bin/env python3
"""把 data/knowledge/ 的衛教 txt 轉成 Bedrock Knowledge Base 的上傳版本。

為什麼需要這一步：原始 txt 開頭固定兩行是 metadata（`標題:` / `來源:`），
但 Bedrock 讀 S3 物件就是讀純文字——那兩行會被當成正文一起切塊、一起算進向量。
後果有兩個：第一塊混著一段 URL，語意被稀釋；agent 要標來源時得從正文裡撈，
而不是從結構化欄位讀。

正解是 Bedrock 的 metadata sidecar：物件旁放一個同名的 `<檔名>.metadata.json`，
Bedrock 會把它當 metadata 而不是內容。所以這支腳本產出的是「乾淨正文 + sidecar」：

    build/kb-upload/高血壓.txt                 ← 只有正文，去掉開頭兩行與 BOM
    build/kb-upload/高血壓.txt.metadata.json   ← 標題與來源

原始檔案不動：`data/knowledge/` 底下那份保留開頭兩行，人看得懂出處，git diff
也讀得出是哪一篇。轉換只發生在上傳前的暫存目錄。

`includeForEmbedding` 的取捨：
- `title` 設 true。切塊策略是 FIXED_SIZE，只有第一塊會帶到標題，第二塊之後
  就沒有任何主題線索了（一段講「每天量兩次」的文字，看不出是在講高血壓還是糖尿病）。
  設 true 讓標題參與每一塊的 embedding，補回這個線索。
- `source` 設 false。URL 對語意比對是純雜訊，但要留著給 agent 引用來源，
  所以進 metadata 但不進 embedding。

用法：scripts/build_kb_upload.py [來源目錄] [輸出目錄]
      預設 data/knowledge → build/kb-upload
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "data" / "knowledge"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "kb-upload"

TITLE_PREFIX = "標題:"
SOURCE_PREFIX = "來源:"


class KbFormatError(Exception):
    """檔案不符合「標題:／來源:／空行／正文」格式。"""


def parse_kb_file(path: Path) -> tuple[str, str, str]:
    """解析開頭兩行 metadata 與正文，回傳 (title, source, body)。

    用 utf-8-sig 讀：29 份裡有 22 份帶 UTF-8 BOM（來源網站另存的產物）。
    不處理的話 BOM 會留在第一行前面，前綴比對會失敗，而且會被一起索引進去。

    格式不符直接拋錯而不是猜：少了來源那行就標不出出處，衛教內容拿不出依據
    比拿錯依據更該擋下來。
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 2:
        raise KbFormatError(f"{path.name}：不足兩行，缺少 metadata 標頭")
    if not lines[0].startswith(TITLE_PREFIX):
        raise KbFormatError(f"{path.name}：第一行不是「{TITLE_PREFIX}」")
    if not lines[1].startswith(SOURCE_PREFIX):
        raise KbFormatError(f"{path.name}：第二行不是「{SOURCE_PREFIX}」")

    title = lines[0].removeprefix(TITLE_PREFIX).strip()
    source = lines[1].removeprefix(SOURCE_PREFIX).strip()
    body = "\n".join(lines[2:]).strip()

    if not title:
        raise KbFormatError(f"{path.name}：標題為空")
    if not body:
        raise KbFormatError(f"{path.name}：正文為空")
    return title, source, body


def build_metadata(title: str, source: str) -> dict:
    """組 Bedrock 的 metadata sidecar 內容。

    用帶型別的寫法（`value.type` / `includeForEmbedding`）而不是扁平的
    `{"key": "value"}`：扁平版沒辦法指定哪些欄位要參與 embedding，而這裡的
    重點正是「標題要進 embedding、URL 不要」。
    """
    return {
        "metadataAttributes": {
            "title": {
                "value": {"type": "STRING", "stringValue": title},
                "includeForEmbedding": True,
            },
            "source": {
                "value": {"type": "STRING", "stringValue": source},
                "includeForEmbedding": False,
            },
        }
    }


def main() -> int:
    source_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    if not source_dir.is_dir():
        print(f"[錯誤] 來源目錄不存在：{source_dir}", file=sys.stderr)
        return 1

    txt_files = sorted(source_dir.glob("*.txt"))
    if not txt_files:
        print(f"[錯誤] {source_dir} 底下沒有 .txt，KB 會是空的", file=sys.stderr)
        return 1

    # 整個重建而不是增量更新：sync_kb.sh 會用 --delete 同步，暫存目錄留著上一次的
    # 殘留檔就會被一起推上 S3。全部重來最不容易留下已經改名或刪掉的舊文件。
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    errors: list[str] = []
    for path in txt_files:
        try:
            title, source, body = parse_kb_file(path)
        except KbFormatError as exc:
            errors.append(str(exc))
            continue

        # 正文寫成不帶 BOM 的 UTF-8，句尾補換行（POSIX 慣例，也避免最後一段
        # 與下次比對時出現無謂差異）
        (output_dir / path.name).write_text(body + "\n", encoding="utf-8")
        (output_dir / f"{path.name}.metadata.json").write_text(
            json.dumps(build_metadata(title, source), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if errors:
        # 一次列完再退出，不要改一個跑一次
        print(f"[錯誤] {len(errors)} 份檔案格式不符，未產出任何內容：", file=sys.stderr)
        for message in errors:
            print(f"  - {message}", file=sys.stderr)
        shutil.rmtree(output_dir)
        return 1

    print(f"已產出 {len(txt_files)} 份正文 + {len(txt_files)} 份 metadata → {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
