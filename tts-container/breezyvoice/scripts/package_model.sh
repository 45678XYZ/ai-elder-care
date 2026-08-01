#!/bin/bash
# 打包 BreezyVoice 的 SageMaker model artifact 並上傳 S3。
#
# BreezyVoice 是 zero-shot 模型，音色完全來自參考音檔，因此「App 的聲音是誰」在這一步
# 就決定了：參考音檔與其逐字稿會一起封進 artifact，執行期不再更動，App 也無從指定。
# 換聲音＝重新打包並重建 endpoint。
#
# 用法：
#   ./package_model.sh \
#     --prompt-audio /path/to/voice.wav \
#     --prompt-text "參考音檔的逐字稿" \
#     --bucket <tts_model_artifact_bucket>
set -euo pipefail

MODEL_ID="MediaTek-Research/BreezyVoice"
REVISION="main"
SPEAKER="default"
PROMPT_AUDIO=""
PROMPT_TEXT=""
BUCKET=""
KEY="tts/breezyvoice/model.tar.gz"

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

echo "==> 下載模型權重 ${MODEL_ID}@${REVISION}"
python3 - "$MODEL_ID" "$REVISION" "$WORKDIR/breezyvoice" <<'PY'
import sys
from huggingface_hub import snapshot_download

model_id, revision, target = sys.argv[1:4]
snapshot_download(
    repo_id=model_id,
    revision=revision,
    local_dir=target,
    # artifact 只需要推論用檔案，範例音檔與說明文件不必進 tar。
    ignore_patterns=["*.md", ".gitattributes", "data/*"],
)
PY

echo "==> 正規化聲紋參考音檔（16 kHz 單聲道）"
# CosyVoice frontend 以 16 kHz 載入 prompt；先轉好可避免執行期再做一次重採樣。
mkdir -p "$WORKDIR/speakers/$SPEAKER"
ffmpeg -hide_banner -loglevel error -y \
  -i "$PROMPT_AUDIO" -ac 1 -ar 16000 -c:a pcm_s16le \
  "$WORKDIR/speakers/$SPEAKER/prompt.wav"
printf '%s' "$PROMPT_TEXT" > "$WORKDIR/speakers/$SPEAKER/prompt.txt"

PROMPT_SECONDS="$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$WORKDIR/speakers/$SPEAKER/prompt.wav" 2>/dev/null || echo 0)"
echo "    參考音檔長度：${PROMPT_SECONDS}s（建議 5–15 秒、單一講者、無背景音）"

echo "==> 打包 model.tar.gz"
ARCHIVE="$WORKDIR/model.tar.gz"
tar -czf "$ARCHIVE" -C "$WORKDIR" breezyvoice speakers

echo "==> 上傳 s3://${BUCKET}/${KEY}"
aws s3 cp "$ARCHIVE" "s3://${BUCKET}/${KEY}"

echo
echo "完成。填入 terraform.tfvars："
echo "  tts_breezyvoice_model_data_url = \"s3://${BUCKET}/${KEY}\""
