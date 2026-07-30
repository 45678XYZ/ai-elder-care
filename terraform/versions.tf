terraform {
  required_version = ">= 1.8"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # >= 6.0：aws_s3vectors_* 與 Knowledge Base 的 S3_VECTORS storage type 只有 6.x 才有
      version = "~> 6.0"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}
