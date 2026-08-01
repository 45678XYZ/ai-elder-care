#!/bin/bash
# 打包 OmniVoice 的 SageMaker model artifact 並上傳 S3。
#
# OmniVoice 是 zero-shot 模型，音色完全來自參考音檔，因此「App 的客語聲音是誰」在這一步
# 就決定了：參考音檔與其逐字稿會一起封進 artifact，執行期不再更動。
#
# 模型是 gated repository：需要已取得存取權的 Hugging Face token。token 只在這裡短暫使用，
# 絕不進入映像、SageMaker environment 或 Lambda（見 docs/asr/model-catalog.md 的存取規則）。
#
# 用法：
#   HF_TOKEN=hf_xxx ./package_model.sh \
#     --prompt-audio /path/to/hakka_voice.wav \
#     --prompt-text "參考音檔的客語逐字稿" \
#     --bucket <tts_model_artifact_bucket>
set -euo pipefail

MODEL_ID="formospeech/omnivoice-hakka-community-1"
REVISION="main"
SPEAKER="default"
PROMPT_AUDIO=""
PROMPT_TEXT=""
BUCKET=""
KEY="tts/omnivoice/model.tar.gz"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-id) MODEL_ID="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --speaker) SPEAKER="$2"; shift 2 ;;
    --prompt-audio) PROMPT_AUDIO="$2"; shift 2 ;;
    --prompt-text) PROMPT_TEXT="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PROMPT_AUDIO" ]] || { echo "missing required argument: --prompt-audio" >&2; exit 2; }
[[ -n "$PROMPT_TEXT" ]] || { echo "missing required argument: --prompt-text" >&2; exit 2; }
[[ -n "$BUCKET" ]] || { echo "missing required argument: --bucket" >&2; exit 2; }
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN is required for this gated model" >&2; exit 2; }

if [[ ! -f "$PROMPT_AUDIO" ]]; then
  echo "prompt audio not found: $PROMPT_AUDIO" >&2
  exit 2
fi

for tool in aws ffmpeg tar; do
  command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done
python3 -c "import huggingface_hub" 2>/dev/null \
  || { echo "pip install huggingface_hub is required" >&2; exit 2; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> 下載模型權重 ${MODEL_ID}@${REVISION}（gated）"
python3 - "$MODEL_ID" "$REVISION" "$WORKDIR/omnivoice" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

model_id, revision, target = sys.argv[1:4]
snapshot_download(
    repo_id=model_id,
    revision=revision,
    local_dir=target,
    token=os.environ["HF_TOKEN"],
    ignore_patterns=["*.md", ".gitattributes"],
)
PY

echo "==> 正規化聲紋參考音檔（16 kHz 單聲道）"
mkdir -p "$WORKDIR/speakers/$SPEAKER"
ffmpeg -hide_banner -loglevel error -y \
  -i "$PROMPT_AUDIO" -ac 1 -ar 16000 -c:a pcm_s16le \
  "$WORKDIR/speakers/$SPEAKER/prompt.wav"
printf '%s' "$PROMPT_TEXT" > "$WORKDIR/speakers/$SPEAKER/prompt.txt"

echo "==> 打包 model.tar.gz"
ARCHIVE="$WORKDIR/model.tar.gz"
tar -czf "$ARCHIVE" -C "$WORKDIR" omnivoice speakers

echo "==> 上傳 s3://${BUCKET}/${KEY}"
aws s3 cp "$ARCHIVE" "s3://${BUCKET}/${KEY}"

echo
echo "完成。填入 terraform.tfvars："
echo "  tts_omnivoice_model_data_url = \"s3://${BUCKET}/${KEY}\""
