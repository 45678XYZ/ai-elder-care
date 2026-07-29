# S3 Vectors：UCO 概念向量索引
#
# batch extractor 在分類前先以 Bedrock embedding 取查詢向量，向此索引取 Top-K 候選概念，
# 再依 concept_id 取 sub-chunk 最高相似度聚合（見 docs/feature_events-extraction.md 決策 B）。
#
# 索引的每個屬性在建立後都不可變更（維度、距離度量、資料型別），因此索引名稱帶模型與維度：
# 比較不同 embedding 模型時各建一份並存，切換只改 Lambda 的 CONCEPT_VECTOR_INDEX。
# 向量內容由 backend/scripts/build_concept_vector_index.py 寫入——那是資料填充，
# 內容會隨本體論版本變動，不適合由 Terraform 管理。

resource "aws_s3vectors_vector_bucket" "concepts" {
  vector_bucket_name = var.concept_vector_bucket

  encryption_configuration {
    # 加密設定在建立後不可變更；概念向量來自公開本體論，SSE-S3 已足夠
    sse_type = "AES256"
  }

  tags = {
    Purpose = "uco-concept-retrieval"
  }
}

resource "aws_s3vectors_index" "concepts" {
  vector_bucket_name = aws_s3vectors_vector_bucket.concepts.vector_bucket_name
  index_name         = var.concept_vector_index

  data_type = "float32"

  # 必須與 embedding 模型輸出維度一致，否則 put_vectors 會 DimensionMismatch
  dimension = var.embedding_dim

  # 檢索端以「相似度 = 1 - distance」換算，對應 cosine
  distance_metric = "cosine"

  tags = {
    EmbeddingModel = var.embedding_model_id
  }
}

# Lambda 查詢索引的最小權限。帶 returnMetadata 查詢時必須同時具備 GetVectors，
# 否則回 403；concept_id 需要參與聚合，所以一定要能讀 metadata。
data "aws_iam_policy_document" "concept_vector_read" {
  statement {
    sid    = "QueryConceptVectorIndex"
    effect = "Allow"

    actions = [
      "s3vectors:QueryVectors",
      "s3vectors:GetVectors",
    ]

    resources = [
      aws_s3vectors_vector_bucket.concepts.vector_bucket_arn,
      aws_s3vectors_index.concepts.index_arn,
    ]
  }
}

resource "aws_iam_policy" "concept_vector_read" {
  name        = "${var.project_name}-concept-vector-read"
  description = "batch extractor 查詢 UCO 概念向量索引的最小權限"
  policy      = data.aws_iam_policy_document.concept_vector_read.json
}

# embedding 與對話模型呼叫權限。
# 模型走 global／區域 inference profile 時，實際被叫用的 foundation model ARN 會隨
# 路由的目的區域改變，因此這裡不逐一列出模型 ARN；改由 CONCEPT/BEDROCK 環境變數控制
# 實際使用哪個模型，並以帳號層級的模型存取權（Bedrock model access）把關。
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid    = "InvokeExtractionModels"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "bedrock_invoke" {
  name        = "${var.project_name}-bedrock-invoke"
  description = "萃取 pipeline 呼叫 Bedrock 對話與 embedding 模型"
  policy      = data.aws_iam_policy_document.bedrock_invoke.json
}
