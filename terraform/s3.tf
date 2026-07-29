# S3 Buckets

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
# 2. Bedrock 衛教知識庫 Bucket（RAG 資料來源）
# =============================================================================

resource "aws_s3_bucket" "knowledge_base" {
  bucket        = "${var.project_name}-kb-data"
  force_destroy = true

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 封鎖所有公開存取
resource "aws_s3_bucket_public_access_block" "kb_public_block" {
  bucket = aws_s3_bucket.knowledge_base.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
