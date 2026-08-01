# Bedrock Knowledge Base（衛教知識庫，RAG）
#
# 資料來源：衛教文件 S3 bucket（見 s3.tf）
# 向量儲存：S3 Vectors（便宜、只需語意檢索，無 hybrid search 需求）
# Embedding：Cohere Embed Multilingual v3（KB 全為繁中衛教文件，多語模型檢索品質較佳；
#            需先在 Bedrock console 開通 model access，見 kb_embedding_model_id）

locals {
  kb_embedding_model_arn = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.kb_embedding_model_id}"

  # Titan v2 是目前唯一支援自選維度的 embedding 模型；其餘（Cohere、Titan v1）維度固定，
  # 帶了維度設定會被 CreateKnowledgeBase 拒絕。
  kb_embedding_dimension_configurable = startswith(var.kb_embedding_model_id, "amazon.titan-embed-text-v2")
}

# --- 向量儲存：S3 Vectors ---

resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${var.project_name}-kb-vectors"

  encryption_configuration {
    sse_type = "AES256"
  }
}

resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "${var.project_name}-kb-index"

  data_type       = "float32"
  dimension       = var.kb_embedding_dimension
  distance_metric = "cosine"

  # chunk 原文塞進 AMAZON_BEDROCK_TEXT metadata，內容長，設為 non-filterable
  # 才不會撞上可篩選 metadata 的大小上限
  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT"]
  }
}

# --- KB 服務角色 ---

resource "aws_iam_role" "kb" {
  name = "${var.project_name}-kb-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        # KB 建立前還沒有實際 ID，用萬用字元鎖同帳號下的 knowledge-base 資源，防混淆代理
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:knowledge-base/*"
        }
      }
    }]
  })
}

# 最小權限：embedding 呼叫只給那顆模型
resource "aws_iam_role_policy" "kb_invoke_model" {
  name = "invoke-embedding-model"
  role = aws_iam_role.kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:InvokeModel"
      Resource = local.kb_embedding_model_arn
    }]
  })
}

# 最小權限：讀文件只給資料來源 bucket
resource "aws_iam_role_policy" "kb_read_documents" {
  name = "read-kb-documents"
  role = aws_iam_role.kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.kb_documents.arn
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.kb_documents.arn}/*"
      }
    ]
  })
}

# 最小權限：向量讀寫只給那個 index
resource "aws_iam_role_policy" "kb_vector_index" {
  name = "rw-kb-vector-index"
  role = aws_iam_role.kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3vectors:GetVectors",
        "s3vectors:PutVectors",
        "s3vectors:QueryVectors",
        "s3vectors:ListVectors",
        "s3vectors:DeleteVectors",
      ]
      Resource = aws_s3vectors_index.kb.index_arn
    }]
  })
}

# --- Knowledge Base ---

resource "aws_bedrockagent_knowledge_base" "kb" {
  name     = "${var.project_name}-kb"
  role_arn = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = local.kb_embedding_model_arn

      # 只有 Titan v2 能自選輸出維度（256／512／1024）。對 Cohere 這種固定維度的模型送出
      # 這個區塊，CreateKnowledgeBase 會回 400：
      #   The specified model ... does not support configurable dimensions.
      # 因此依模型決定要不要帶；固定維度的模型改由 kb_embedding_dimension 對齊索引維度即可
      # （見本檔上方 aws_s3vectors_index.kb，Cohere multilingual v3 原生就是 1024）。
      dynamic "embedding_model_configuration" {
        for_each = local.kb_embedding_dimension_configurable ? [1] : []

        content {
          bedrock_embedding_model_configuration {
            dimensions = var.kb_embedding_dimension
          }
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"

    # index_arn 與 vector_bucket_arn 互斥：帶 bucket 是請 Bedrock 自己在該 bucket 建索引
    # （要另外給 index_name），帶 index_arn 則是指定既有索引。本檔上面已經明確建了
    # aws_s3vectors_index.kb（維度、距離度量、non-filterable metadata 都要自己控），
    # 所以走 index_arn；bucket 由索引自己帶出來，不必也不能再指定一次。
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  depends_on = [
    aws_iam_role_policy.kb_invoke_model,
    aws_iam_role_policy.kb_read_documents,
    aws_iam_role_policy.kb_vector_index,
  ]
}

# --- 資料來源：衛教文件 bucket，固定大小分塊 ---

resource "aws_bedrockagent_data_source" "kb_documents" {
  knowledge_base_id    = aws_bedrockagent_knowledge_base.kb.id
  name                 = "${var.project_name}-kb-documents"
  data_deletion_policy = "RETAIN"

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn = aws_s3_bucket.kb_documents.arn
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"

      fixed_size_chunking_configuration {
        max_tokens         = var.kb_chunk_max_tokens
        overlap_percentage = var.kb_chunk_overlap_percentage
      }
    }
  }
}
