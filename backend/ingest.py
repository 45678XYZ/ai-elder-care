"""階段一：把 kb/ 裡的 txt 知識庫建成 Chroma 向量庫。

用法：python ingest.py
"""

from pathlib import Path

import chromadb

from embedding import get_embedding_function

BASE_DIR = Path(__file__).resolve().parent
KB_DIR = BASE_DIR / "kb"
CHROMA_DIR = BASE_DIR / "chroma_db"
COLLECTION_NAME = "kb_collection"  # Chroma 要求名稱至少 3 字元，"kb" 太短

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
CJK_RATIO_MIN = 0.05


def cjk_ratio(text: str) -> float:
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk / max(len(text), 1)


def parse_kb_file(path: Path) -> tuple[str, str, str]:
    """解析 標題:/來源: 開頭兩行 + 正文，回傳 (title, source, body)。"""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 2 or not lines[0].startswith("標題:") or not lines[1].startswith("來源:"):
        raise ValueError(f"{path.name}：缺少「標題:」/「來源:」開頭兩行，無法解析 metadata")

    title = lines[0].removeprefix("標題:").strip()
    source = lines[1].removeprefix("來源:").strip()
    body = "\n".join(lines[2:]).strip()
    return title, source, body


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if overlap >= size:
        raise ValueError(f"CHUNK_OVERLAP({overlap}) 必須小於 CHUNK_SIZE({size})，否則切塊會卡死")

    if len(text) <= size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def main() -> None:
    if not KB_DIR.exists():
        raise SystemExit(f"找不到知識庫資料夾：{KB_DIR}")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        COLLECTION_NAME, embedding_function=get_embedding_function()
    )

    txt_files = sorted(KB_DIR.glob("*.txt"))
    if not txt_files:
        raise SystemExit(f"{KB_DIR} 底下沒有任何 .txt 檔")

    total_chunks = 0
    cjk_warnings: list[str] = []

    for path in txt_files:
        title, source, body = parse_kb_file(path)

        ratio = cjk_ratio(body)
        if ratio < CJK_RATIO_MIN:
            cjk_warnings.append(f"{path.name}（中文字元佔比 {ratio:.1%}）")
            print(f"[CJK 警告] 跳過 {path.name}：中文字元佔比僅 {ratio:.1%}，內容可能因字型編碼壞掉而遺失，請人工檢查後再重跑")
            continue

        chunks = chunk_text(body)
        if not chunks:
            print(f"[警告] {path.name} 正文為空，已跳過")
            continue

        ids = [f"{path.stem}-第{i + 1}塊" for i in range(len(chunks))]
        metadatas = [{"title": title, "source": source} for _ in chunks]
        collection.add(documents=chunks, metadatas=metadatas, ids=ids)
        total_chunks += len(chunks)

    print()
    print(f"完成：共處理 {len(txt_files)} 個檔案，存入 {total_chunks} 個 chunk")
    if cjk_warnings:
        print(f"觸發 CJK 警告並被跳過的檔案（{len(cjk_warnings)} 個，未收錄進向量庫）：")
        for w in cjk_warnings:
            print(f"  - {w}")
    else:
        print("沒有檔案觸發 CJK 警告")


if __name__ == "__main__":
    main()
