#!/usr/bin/env bash
# 上傳衛教文件到 Knowledge Base 資料來源 bucket，並觸發 ingestion job 重建索引。
# terraform 只建資源，資料搬移由這支腳本負責。
#
# 不直接上傳 data/knowledge/：那裡的 txt 開頭兩行是 `標題:` / `來源:`，Bedrock 會
# 當成正文一起索引。先經 build_kb_upload.py 轉成「乾淨正文 + .metadata.json sidecar」
# 再上傳，理由詳見那支腳本的說明。
#
# 用法：scripts/sync_kb.sh [文件目錄，預設 data/knowledge]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${1:-${REPO_ROOT}/data/knowledge}"
UPLOAD_DIR="${REPO_ROOT}/build/kb-upload"

# python3 / python / py：Windows 的 Git Bash 通常只有 py，CI 與 macOS 則是 python3
PYTHON=""
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
done
if [ -z "$PYTHON" ]; then
  echo "找不到 python，無法產生 metadata sidecar" >&2
  exit 1
fi

echo "產生上傳版本（去標頭 + metadata sidecar）"
"$PYTHON" "${REPO_ROOT}/scripts/build_kb_upload.py" "${SOURCE_DIR}" "${UPLOAD_DIR}"

cd "${REPO_ROOT}/terraform"

BUCKET=$(terraform output -raw kb_documents_bucket)
KB_ID=$(terraform output -raw kb_knowledge_base_id)
DATA_SOURCE_ID=$(terraform output -raw kb_data_source_id)

echo "上傳 ${UPLOAD_DIR} → s3://${BUCKET}/"
aws s3 sync "${UPLOAD_DIR}" "s3://${BUCKET}/" --delete

echo "觸發 ingestion job（knowledge base ${KB_ID}, data source ${DATA_SOURCE_ID}）"
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "${KB_ID}" \
  --data-source-id "${DATA_SOURCE_ID}"
