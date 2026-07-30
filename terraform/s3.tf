# S3
#   - TTS 音檔 bucket（/chat 回覆，presigned URL 15 分鐘；設定生命週期自動清除）→ 不在此範圍
#   - 衛教文件 bucket（Bedrock Knowledge Base 資料來源，部署時上傳 data/knowledge/）
#
# data.aws_caller_identity.current 見 providers.tf

# 衛教文件 bucket：bucket 名稱全域唯一，帶 account id 避免撞名
resource "aws_s3_bucket" "kb_documents" {
  bucket = "${var.project_name}-kb-documents-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "kb_documents" {
  bucket = aws_s3_bucket.kb_documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kb_documents" {
  bucket = aws_s3_bucket.kb_documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "kb_documents" {
  bucket = aws_s3_bucket.kb_documents.id

  versioning_configuration {
    status = "Enabled"
  }
}
