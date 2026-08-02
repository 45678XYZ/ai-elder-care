#!/bin/bash
# 在 GPU 機器上實際起容器，驗證 /ping 與 /invocations。
#
# 為什麼需要這一步：Dockerfile 的建置期煙霧測試驗得到相依版本，但驗不到「載入
# model.tar.gz 裡的權重」——權重不在映像裡。BreezyVoice 前兩次都是相依版本沒問題、
# 卻在載入模型時才炸，而那時已經是 SageMaker endpoint 的 ping health check 在判死：
# 一次要付 25 分鐘的 apply 加上 GPU 機時。在這裡跑一次只要幾分鐘。
#
# 兩種模式：
#
#   （預設）完整驗證 — 需要 NVIDIA GPU 與 nvidia-container-toolkit，instance type 要與
#   endpoint 一致（ml.g4dn.4xlarge → g4dn.4xlarge）。起 server、打 /ping 與 /invocations，
#   等同 SageMaker 的判定方式。
#
#   --skip-gpu — 沒有 GPU 機器時的次佳選擇。不起 server，改為在容器內載入 model artifact
#   裡的 cosyvoice.yaml。這不是形式檢查：hyperpyyaml 會實際實例化 yaml 宣告的 llm／flow／
#   hift／tokenizer 物件，等於走完整個模型建構路徑，只差權重載入與 .to(cuda)。相依版本
#   不合、上游模組 import 失敗、yaml 解析器 API 變動都會在這裡現形——BreezyVoice 迄今
#   兩次 endpoint 失敗都屬於這個範圍。**但它驗不到顯存是否足夠與實際合成品質。**
#
# 用法：./verify_container.sh --image <uri> --model-data s3://.../model.tar.gz [--skip-gpu]
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
IMAGE=""
MODEL_DATA=""
PORT=8080
SKIP_GPU=0
CONTAINER_NAME="breezyvoice-verify"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --model-data) MODEL_DATA="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --skip-gpu) SKIP_GPU=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$IMAGE" ]] || { echo "--image is required" >&2; exit 2; }
[[ -n "$MODEL_DATA" ]] || { echo "--model-data is required" >&2; exit 2; }

WORKDIR="$(mktemp -d)"
MODEL_DIR="$WORKDIR/model"
mkdir -p "$MODEL_DIR"

cleanup() {
  local status=$?
  echo "==> 收拾容器"
  docker logs "$CONTAINER_NAME" > "$WORKDIR/container.log" 2>&1 || true
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  if [[ $status -ne 0 ]]; then
    # 失敗時保留 workdir：容器 log 是唯一能看出啟動失敗原因的東西。
    echo "驗證失敗。容器 log：$WORKDIR/container.log"
  else
    rm -rf "$WORKDIR"
  fi
}
trap cleanup EXIT

echo "==> 取得 model artifact"
aws s3 cp "$MODEL_DATA" "$WORKDIR/model.tar.gz" --region "$REGION" --only-show-errors
tar -xzf "$WORKDIR/model.tar.gz" -C "$MODEL_DIR"

echo "==> 登入 ECR 並拉映像"
REGISTRY="${IMAGE%%/*}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker pull "$IMAGE"

if [[ $SKIP_GPU -eq 1 ]]; then
  echo "==> 無 GPU 模式：在容器內載入 cosyvoice.yaml（會實例化模型物件）"
  docker run --rm --name "$CONTAINER_NAME" \
    -v "$MODEL_DIR:/opt/ml/model:ro" \
    "$IMAGE" \
    python3 -c "
import sys

# 必須先走 app 自己的路徑設定再載 yaml。cosyvoice.flow.flow_matching 依賴
# third_party/Matcha-TTS，那條路徑是由 _ensure_import_path() 加進 sys.path 的；
# 直接呼叫 load_hyperpyyaml 會少掉這一步，得到與實際執行不符的 ModuleNotFoundError。
from app.synthesizer import _ensure_import_path
_ensure_import_path()

from hyperpyyaml import load_hyperpyyaml

path = '/opt/ml/model/breezyvoice/cosyvoice.yaml'
with open(path) as handle:
    configs = load_hyperpyyaml(handle)

required = {'llm', 'flow', 'hift', 'get_tokenizer', 'feat_extractor', 'allowed_special'}
missing = required - set(configs)
if missing:
    print('cosyvoice.yaml 少了這些鍵:', sorted(missing), file=sys.stderr)
    sys.exit(1)
print('模型設定載入成功，物件皆已實例化:', sorted(required))
"
  echo
  echo "相依與模型建構路徑通過。"
  echo "注意：這一輪沒有驗到顯存是否足夠、/ping 是否回應、以及合成品質。"
  exit 0
fi

echo "==> 啟動容器"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
# SageMaker 以唯讀方式掛載 /opt/ml/model 並用 `serve` 當進入點；這裡完全照做，
# 才驗得到「權重路徑寫死在別處」這類只有正式掛載方式才會踩到的問題。
docker run -d --name "$CONTAINER_NAME" \
  --gpus all \
  -p "${PORT}:8080" \
  -v "$MODEL_DIR:/opt/ml/model:ro" \
  "$IMAGE" serve

echo "==> 等待 /ping（最多 10 分鐘）"
deadline=$((SECONDS + 600))
ready=0
while [[ $SECONDS -lt $deadline ]]; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "容器已退出，啟動失敗："
    docker logs "$CONTAINER_NAME" 2>&1 | tail -40
    exit 1
  fi
  if curl -fsS "http://localhost:${PORT}/ping" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done

if [[ $ready -ne 1 ]]; then
  echo "/ping 在期限內沒有回應（SageMaker 也會以同樣的理由判死）："
  docker logs "$CONTAINER_NAME" 2>&1 | tail -40
  exit 1
fi
echo "    /ping OK"

echo "==> 打一次 /invocations"
OUT="$WORKDIR/out.mp3"
http_code=$(curl -sS -o "$OUT" -w '%{http_code}' \
  -X POST "http://localhost:${PORT}/invocations" \
  -H 'Content-Type: application/json' \
  -d '{"text":"今仔日天氣真好，出門記得帶傘。","language":"zh-TW"}')

if [[ "$http_code" != "200" ]]; then
  echo "/invocations 回 $http_code："
  cat "$OUT"; echo
  docker logs "$CONTAINER_NAME" 2>&1 | tail -40
  exit 1
fi

size=$(wc -c < "$OUT")
# 合成失敗有時會回一段極短的空音訊而不是錯誤；用長度擋掉這種「200 但沒東西」的情況。
if [[ "$size" -lt 2000 ]]; then
  echo "/invocations 回 200 但音訊只有 ${size} bytes，視為失敗"
  exit 1
fi

echo "    /invocations OK（${size} bytes MP3）"
echo
echo "驗證通過。可以更新 tts_breezyvoice_image_uri 並 apply。"
