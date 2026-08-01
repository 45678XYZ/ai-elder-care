# S3
#   - TTS 音檔 bucket（/chat 回覆，presigned URL 15 分鐘；生命週期 1 天後自動清除）
#   - 衛教文件 bucket（Bedrock Knowledge Base 資料來源，部署時上傳 data/knowledge/）
#   - Lambda 部署包 bucket（zip 超過直接上傳上限，改走 S3）
#
# data.aws_caller_identity.current 見 providers.tf

# =============================================================================
# 1. TTS 音檔 Bucket（提供 Presigned URL 讓 Flutter App 播放對話語音）
# =============================================================================

resource "aws_s3_bucket" "audio" {
  bucket = "${var.project_name}-audio"
  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 注意：此 Bucket 不需要 CORS 設定。
# Flutter App 透過原生 HTTP 客戶端存取 Presigned URL，不走瀏覽器，因此不受 CORS 限制。

# 設定生命週期：1 天後自動刪除（因為 presigned URL 只有 15 分鐘，留著也沒用且浪費錢）
resource "aws_s3_bucket_lifecycle_configuration" "audio_lifecycle" {
  bucket = aws_s3_bucket.audio.id

  rule {
    id     = "auto-delete-expired-audio"
    status = "Enabled"

    expiration {
      days = 1
    }
  }
}

# 封鎖所有公開存取（確保只能透過 Presigned URL 存取，不對外開放）
resource "aws_s3_bucket_public_access_block" "audio_public_block" {
  bucket = aws_s3_bucket.audio.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# =============================================================================
# 2. 衛教文件 Bucket（Bedrock Knowledge Base 資料來源，見 bedrock_kb.tf）
# =============================================================================
# bucket 名稱全域唯一，帶 account id 避免撞名

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

# =============================================================================
# 3. Lambda 部署包 Bucket（見 lambda.tf 的 backend_source_path）
# =============================================================================
# 直接上傳的 zip 上限是 50 MB，但 requirements.txt 帶了 av（內含 FFmpeg）、numpy 與
# soundfile，打包後約 75 MB，CreateFunction 會回 RequestEntityTooLargeException。
# 改由 S3 上傳就沒有這個上限；解壓後 250 MB 的限制仍在，目前約 215 MB。
#
# force_destroy：裡面只有建置產物，砍掉環境時不該為了清 zip 而卡住 destroy。
resource "aws_s3_bucket" "lambda_artifacts" {
  bucket        = "${var.project_name}-lambda-artifacts-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lambda_artifacts" {
  bucket = aws_s3_bucket.lambda_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
