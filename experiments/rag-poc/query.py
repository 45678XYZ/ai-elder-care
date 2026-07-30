"""階段二：CLI 測試入口。

用法：python query.py "高血壓要注意什麼？"
"""

import sys

from rag import answer


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('用法：python query.py "問題"')

    question = sys.argv[1]
    result = answer(question)

    print(f"問題：{question}\n")
    print("=== 撈到的段落 ===")
    for i, chunk in enumerate(result["_retrieved"], start=1):
        preview = chunk["text"][:80].replace("\n", " ")
        print(f"[{i}] 來源：{chunk['title']}（{chunk['source']}）")
        print(f"    {preview}...")
    print()
    print("=== LLM 回答 ===")
    print(result["answer"])
    print()
    print("=== 引用來源 ===")
    for s in result["sources"]:
        print(f"- {s['title']}：{s['url']}")


if __name__ == "__main__":
    main()
