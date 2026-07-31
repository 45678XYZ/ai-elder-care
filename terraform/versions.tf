terraform {
  required_version = ">= 1.8"

  required_providers {
    # 6.24 起支援 S3 Vectors（aws_s3vectors_vector_bucket / aws_s3vectors_index），
    # 概念向量索引因此可納入 IaC，不再需要部署後手動建立
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.24"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}
