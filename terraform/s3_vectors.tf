# S3 Vectors：UCO 概念向量索引
#
# batch extractor 在分類前先以 Bedrock embedding 取查詢向量，向此索引取 Top-K 候選概念，
# 再依 concept_id 取 sub-chunk 最高相似度聚合（見 docs/feature_events-extraction.md 決策 B）。
#
# 為什麼 bucket 與 index 不由 Terraform 建立：
#   本專案釘住 hashicorp/aws ~> 5.0，該版本沒有 aws_s3vectors_* 資源。硬寫上去會讓
#   terraform plan 直接失敗，因此改由部署後的資料填充步驟建立：
#
#     python -m scripts.build_concept_vector_index \
#         --bucket <bucket> --model <model_id> --dim <dim> --create-index
#
#   索引維度在建立時固定，名稱帶模型與維度；比較不同 embedding 模型時各建一份並存，
#   切換只改 Lambda 的 CONCEPT_VECTOR_INDEX 環境變數。
#   升級到支援 S3 Vectors 的 provider 版本後，可把 bucket/index 納管並移除此註記。
#
# 這裡只管 Terraform 能管的部分：Lambda 讀取索引所需的最小權限。

data "aws_iam_policy_document" "concept_vector_read" {
  statement {
    sid    = "QueryConceptVectorIndex"
    effect = "Allow"

    actions = [
      "s3vectors:QueryVectors",
      # 帶 returnMetadata 查詢時必須同時具備 GetVectors，否則回 403
      "s3vectors:GetVectors",
    ]

    resources = [
      "arn:aws:s3vectors:${var.aws_region}:${data.aws_caller_identity.current.account_id}:bucket/${var.concept_vector_bucket}",
      "arn:aws:s3vectors:${var.aws_region}:${data.aws_caller_identity.current.account_id}:bucket/${var.concept_vector_bucket}/index/*",
    ]
  }
}

resource "aws_iam_policy" "concept_vector_read" {
  name        = "${var.project_name}-concept-vector-read"
  description = "batch extractor 查詢 UCO 概念向量索引的最小權限"
  policy      = data.aws_iam_policy_document.concept_vector_read.json
}

# embedding 與對話模型呼叫權限；模型 ID 走變數，換模型不需改 policy 結構
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid    = "InvokeExtractionModels"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:Converse",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "bedrock_invoke" {
  name        = "${var.project_name}-bedrock-invoke"
  description = "萃取 pipeline 呼叫 Bedrock 對話與 embedding 模型"
  policy      = data.aws_iam_policy_document.bedrock_invoke.json
}
