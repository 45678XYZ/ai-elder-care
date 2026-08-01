#!/bin/bash
# 打包 FormoSpeech Whisper-v3 的 SageMaker model artifact 並上傳 S3。
#
# 六腔共用同一份 artifact 與同一個映像；腔調由 terraform 注入的 FORMO_PROMPT_ID 決定，
# 所以這支腳本只需要跑一次。
#
# 模型是 gated repository：需要已取得存取權的 Hugging Face token。依
# docs/asr/model-catalog.md 的存取規則，token 只在這裡短暫使用，絕不進入映像、
# SageMaker environment 或 Lambda。
#
# 用法：
#   HF_TOKEN=hf_xxx ./package_model.sh --bucket <asr_model_artifact_bucket>
set -euo pipefail

MODEL_ID="formospeech/whisper-large-v3-taiwanese-hakka"
REVISION="main"
BUCKET=""
KEY="asr/formospeech/model.tar.gz"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-id) MODEL_ID="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$BUCKET" ]] || { echo "missing required argument: --bucket" >&2; exit 2; }
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN is required for this gated model" >&2; exit 2; }

for tool in aws tar; do
  command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done
python3 -c "import huggingface_hub" 2>/dev/null \
  || { echo "pip install huggingface_hub is required" >&2; exit 2; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> 下載模型權重 ${MODEL_ID}@${REVISION}（gated，約 3 GB）"
python3 - "$MODEL_ID" "$REVISION" "$WORKDIR/formospeech" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

model_id, revision, target = sys.argv[1:4]
snapshot_download(
    repo_id=model_id,
    revision=revision,
    local_dir=target,
    token=os.environ["HF_TOKEN"],
    # 只留推論需要的檔案；容器以 use_safetensors=True 載入，.bin 權重是重複的。
    ignore_patterns=["*.md", ".gitattributes", "*.bin"],
)
PY

echo "==> 打包 model.tar.gz"
ARCHIVE="$WORKDIR/model.tar.gz"
tar -czf "$ARCHIVE" -C "$WORKDIR" formospeech

echo "==> 上傳 s3://${BUCKET}/${KEY}"
# 慢速連線下 UploadPart 容易閒置逾時；關掉讀取逾時並拉高重試次數。
AWS_MAX_ATTEMPTS=10 aws s3 cp --cli-read-timeout 0 "$ARCHIVE" "s3://${BUCKET}/${KEY}"

echo
echo "完成。填入 terraform.tfvars："
echo "  asr_formo_model_data_url = \"s3://${BUCKET}/${KEY}\""
