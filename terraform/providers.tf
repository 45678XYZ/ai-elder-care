provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project = var.project_name
    }
  }
}

# 組 ARN 時需要帳號 ID（S3 Vectors 目前沒有對應的 provider 資源可參照）
data "aws_caller_identity" "current" {}
