terraform {
  required_version = ">= 1.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.24"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # state 存 S3 而不是本機：資源名稱多是寫死的，state 一旦遺失，重跑 apply 會撞
  # already exists，只能手動刪或逐一 import。bucket 開了版本控管，改壞可以回滾。
  # 這個 bucket 刻意不納入 terraform 管理——它是 state 自己的家，被自己管會在 destroy 打結。
  backend "s3" {
    bucket  = "e-hakka-care-tfstate-437814057855"
    key     = "e-hakka-care/terraform.tfstate"
    region  = "us-west-2"
    encrypt = true
  }
}
