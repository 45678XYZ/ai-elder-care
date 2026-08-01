#!/bin/bash
# 建置 FormoSpeech Whisper-v3 推論映像並推上 ECR。
#
# 六腔共用這一個映像；腔調由 terraform 注入 FORMO_PROMPT_ID，換腔調不必重建。
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
REPOSITORY="e-hakka-care-asr-formospeech"
TAG="$(date +%Y%m%d)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --repository) REPOSITORY="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v aws >/dev/null || { echo "aws cli is required" >&2; exit 2; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPOSITORY}:${TAG}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 確認 ECR repository"
aws ecr describe-repositories --repository-names "$REPOSITORY" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository \
       --repository-name "$REPOSITORY" \
       --region "$REGION" \
       --image-scanning-configuration scanOnPush=true >/dev/null

echo "==> 登入 ECR"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> 建置 ${IMAGE_URI}"
# SageMaker 一律跑 linux/amd64；在 Apple Silicon 上開發時不指定平台會推出跑不起來的映像。
docker buildx build \
  --platform linux/amd64 \
  --tag "$IMAGE_URI" \
  --push \
  "$(dirname "$SCRIPT_DIR")"

echo
echo "完成。填入 terraform.tfvars："
echo "  asr_formo_image_uri = \"${IMAGE_URI}\""
