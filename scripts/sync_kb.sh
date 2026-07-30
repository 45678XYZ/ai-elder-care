#!/usr/bin/env bash
# 上傳衛教文件到 Knowledge Base 資料來源 bucket，並觸發 ingestion job 重建索引。
# terraform 只建資源，資料搬移由這支腳本負責。
#
# 用法：scripts/sync_kb.sh [文件目錄，預設 data/knowledge]
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../terraform"

SOURCE_DIR="${1:-../data/knowledge}"
BUCKET=$(terraform output -raw kb_documents_bucket)
KB_ID=$(terraform output -raw kb_knowledge_base_id)
DATA_SOURCE_ID=$(terraform output -raw kb_data_source_id)

echo "上傳 ${SOURCE_DIR} → s3://${BUCKET}/"
aws s3 sync "${SOURCE_DIR}" "s3://${BUCKET}/" --delete --exclude "README.md"

echo "觸發 ingestion job（knowledge base ${KB_ID}, data source ${DATA_SOURCE_ID}）"
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "${KB_ID}" \
  --data-source-id "${DATA_SOURCE_ID}"
