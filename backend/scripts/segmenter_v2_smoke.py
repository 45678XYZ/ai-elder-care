"""離線煙霧測試：不打 AWS 就把整條工作流跑通。

    python scripts/segmenter_v2_smoke.py

用確定性的假 embedding 填快取，再依序跑訓練、導出、評測。驗的是「程式接得起來、
artifact 契約對得上、gate 判定會動」，**不是模型品質**——品質數字一律要用真實
embedding 與人工標註測試集重跑。

跑得過代表：語料解析、特徵抽取（與執行期同一份實作）、GroupKFold、門檻掃描、
artifact 導出與自我驗證、推論、指標與 gate 判定這條鏈是通的。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from training.segmenter_v2 import corpora, paths  # noqa: E402
from training.segmenter_v2.embeddings import EmbeddingCache, stub_vector  # noqa: E402

STUB_MODEL = "stub-embed"
STUB_DIM = 16
TRAIN_LIMIT = 60
DEV_LIMIT = 25


def run(command: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(command[1:])}")
    # 明確指定 utf-8：子行程輸出含中文，Windows 預設 cp950 會在解碼時直接炸掉
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )
    sys.stdout.write((result.stdout or "")[-3000:])
    if result.returncode != 0:
        sys.stdout.write((result.stderr or "")[-3000:])
        raise SystemExit(f"步驟失敗（exit {result.returncode}）")


def main() -> int:
    source_train = paths.CORPORA_DIR / "train_en.jsonl"
    source_dev = paths.CORPORA_DIR / "dev_en.jsonl"
    if not source_train.is_file():
        raise SystemExit("請先跑 scripts/segmenter_v2_prepare_corpora.py")

    with tempfile.TemporaryDirectory(prefix="segmenter_v2_smoke_") as tmp:
        work = Path(tmp)
        train = corpora.load_dialogues(source_train)[:TRAIN_LIMIT]
        dev = corpora.load_dialogues(source_dev)[:DEV_LIMIT]
        corpora.save_dialogues(work / "train.jsonl", train)
        corpora.save_dialogues(work / "dev.jsonl", dev)
        print(f"子集：訓練 {len(train)} 段 / 開發 {len(dev)} 段")

        cache = EmbeddingCache(work / "cache" / f"{STUB_MODEL}.jsonl", STUB_MODEL, STUB_DIM)
        texts = [turn.text for dialogue in train + dev for turn in dialogue.turns]
        cache.put_many((text, stub_vector(text, STUB_DIM)) for text in dict.fromkeys(texts))
        print(f"假 embedding 快取 {len(cache)} 筆")

        python = sys.executable
        run(
            [
                python,
                "scripts/segmenter_v2_train.py",
                "--train", str(work / "train.jsonl"),
                "--dev", str(work / "dev.jsonl"),
                "--model", STUB_MODEL,
                "--dim", str(STUB_DIM),
                "--cache-dir", str(work / "cache"),
                "--out", str(work / "out"),
                "--folds", "3",
            ],
            BACKEND_DIR,
        )

        artifact_path = work / "out" / "pairwise_v2.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["embedding_dim"] == STUB_DIM
        assert artifact["trees"], "artifact 沒有決策樹"
        assert artifact["model_card"]["split"] == "GroupKFold by dialogue_id"
        print(f"\nartifact 檢查通過：{len(artifact['trees'])} 棵樹，門檻 {artifact['threshold']}")

        run(
            [
                python,
                "scripts/segmenter_v2_evaluate.py",
                "--test", str(work / "dev.jsonl"),
                "--artifact", str(artifact_path),
                "--model", STUB_MODEL,
                "--dim", str(STUB_DIM),
                "--cache-dir", str(work / "cache"),
                "--out", str(work / "out" / "eval_report.json"),
            ],
            BACKEND_DIR,
        )

        report = json.loads((work / "out" / "eval_report.json").read_text(encoding="utf-8"))
        assert set(report["results"]) == {"every_3_turns", "embedding_depth", "pairwise_v2"}
        assert "passed" in report["gate"]
        print("\n煙霧測試通過。提醒：假 embedding 的分數沒有意義，gate 判定必須用真實向量與人工標註測試集重跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
