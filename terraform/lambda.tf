# Lambda（Python，程式碼在 backend/src/）
#
# functions：chat / elders / summaries / events / routines / stats / summary_generator
#
# 目前僅建置 auth 需要的 Cognito pre-token-generation trigger。其餘 handler 的統一
# 打包／部署（archive_file 或 build script）、API Gateway 整合與共用 IAM 仍為 TODO。

# --- pre-token-generation trigger：發 ID token 前注入 elder_id claim ---

# 自成一包的單檔 Lambda（不含 src.shared），直接壓縮該檔即可。
data "archive_file" "pre_token" {
  type        = "zip"
  source_file = "${path.module}/../backend/src/handlers/pre_token_generation.py"
  output_path = "${path.module}/build/pre_token_generation.zip"
}

resource "aws_iam_role" "pre_token" {
  name = "${var.project_name}-pre-token-trigger"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "pre_token_logs" {
  role       = aws_iam_role.pre_token.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 最小權限：只讀對應表查 sub→elder_id
resource "aws_iam_role_policy" "pre_token_ddb" {
  name = "read-elder-accounts"
  role = aws_iam_role.pre_token.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "dynamodb:GetItem"
      Resource = aws_dynamodb_table.elder_accounts.arn
    }]
  })
}

resource "aws_lambda_function" "pre_token" {
  function_name = "${var.project_name}-pre-token-trigger"
  role          = aws_iam_role.pre_token.arn
  handler       = "pre_token_generation.handler"
  runtime       = "python3.11"

  filename         = data.archive_file.pre_token.output_path
  source_code_hash = data.archive_file.pre_token.output_base64sha256

  environment {
    variables = {
      ELDER_ACCOUNTS_TABLE = aws_dynamodb_table.elder_accounts.name
    }
  }
}

# 允許此 user pool 呼叫 trigger
resource "aws_lambda_permission" "pre_token_cognito" {
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.accounts.arn
}

# --- 生活記錄（Module B）批次萃取 ---
#
# 三支 Lambda 共用同一個部署包（backend/src），差別只在 handler 與環境變數。
# 部署包內容先由 `python -m scripts.package_lambda` 整理到 terraform/build/backend，
# 因為 backend/ 底下有 .venv 與 tests 不該進包；資產（分類體系、概念 sub-chunk）必須隨包發佈。

data "archive_file" "backend" {
  type        = "zip"
  source_dir  = "${path.module}/build/backend"
  output_path = "${path.module}/build/backend.zip"
}

locals {
  # 萃取行為一律由環境變數驅動，程式不寫死（見 docs/framework.md 後端環境變數）
  extraction_env = {
    TABLE_ELDERS          = aws_dynamodb_table.elders.name
    TABLE_CONVERSATIONS   = aws_dynamodb_table.conversations.name
    TABLE_EVENTS          = aws_dynamodb_table.events.name
    TABLE_ROUTINES        = aws_dynamodb_table.routines.name
    TABLE_DAILY_SUMMARIES = aws_dynamodb_table.daily_summaries.name

    BEDROCK_MODEL_ID            = var.bedrock_model_id
    BEDROCK_CLASSIFIER_MODEL_ID = var.bedrock_classifier_model_id
    BEDROCK_EXTRACTOR_MODEL_ID  = var.bedrock_extractor_model_id
    BEDROCK_CHUNKER_MODEL_ID    = var.bedrock_chunker_model_id

    EMBEDDING_MODEL_ID    = var.embedding_model_id
    EMBEDDING_DIM         = tostring(var.embedding_dim)
    CONCEPT_VECTOR_BUCKET = var.concept_vector_bucket
    CONCEPT_VECTOR_INDEX  = var.concept_vector_index

    EVENT_SLOT_MINUTES = tostring(var.event_slot_minutes)
    CHUNKER_TYPE       = var.chunker_type
    EXTRACTION_MODE    = var.extraction_mode
    RAC_TOP_K          = tostring(var.rac_top_k)

    BATCH_QUEUE_URL = aws_sqs_queue.batch.id
  }
}

resource "aws_iam_role" "extraction" {
  name = "${var.project_name}-extraction"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "extraction_logs" {
  role       = aws_iam_role.extraction.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "extraction_bedrock" {
  role       = aws_iam_role.extraction.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}

resource "aws_iam_role_policy_attachment" "extraction_vectors" {
  role       = aws_iam_role.extraction.name
  policy_arn = aws_iam_policy.concept_vector_read.arn
}

# 最小權限：只給實際會碰到的表與動作。
# events 需要條件式 Put 與 revision enrichment；conversations 需要 session 狀態機與
# sessions-by-state GSI 查詢；elders 只讀 persona 供萃取 prompt 使用。
data "aws_iam_policy_document" "extraction_data" {
  statement {
    sid    = "EventsWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.events.arn,
      "${aws_dynamodb_table.events.arn}/index/*",
    ]
  }

  statement {
    sid    = "ConversationsSessionState"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.conversations.arn,
      "${aws_dynamodb_table.conversations.arn}/index/*",
    ]
  }

  statement {
    sid       = "EldersRead"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.elders.arn]
  }

  statement {
    sid    = "BatchQueue"
    effect = "Allow"

    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]

    resources = [aws_sqs_queue.batch.arn, aws_sqs_queue.batch_dlq.arn]
  }

  statement {
    sid       = "BatchAlerts"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.batch_alerts.arn]
  }
}

resource "aws_iam_role_policy" "extraction_data" {
  name   = "extraction-data-access"
  role   = aws_iam_role.extraction.id
  policy = data.aws_iam_policy_document.extraction_data.json
}

resource "aws_sns_topic" "batch_alerts" {
  name = "${var.project_name}-batch-alerts"
}

resource "aws_lambda_function" "batch_extractor" {
  function_name = "${var.project_name}-batch-extractor"
  role          = aws_iam_role.extraction.arn
  handler       = "src.handlers.batch_extractor.handler"
  runtime       = "python3.11"

  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256

  # 一個 session 要跑 chunk 數 × 兩次模型呼叫；timeout 必須小於 SQS visibility timeout
  timeout     = var.batch_lambda_timeout
  memory_size = 1024

  environment {
    variables = merge(local.extraction_env, {
      BATCH_LEASE_SECONDS = tostring(var.batch_lambda_timeout * 2)
    })
  }
}

resource "aws_lambda_event_source_mapping" "batch_extractor" {
  event_source_arn = aws_sqs_queue.batch.arn
  function_name    = aws_lambda_function.batch_extractor.arn

  # 一次一則：單則失敗不牽連其他 session；handler 仍回報 partial batch failure
  batch_size                         = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0
}

resource "aws_lambda_function" "session_closer" {
  function_name = "${var.project_name}-session-closer"
  role          = aws_iam_role.extraction.arn
  handler       = "src.handlers.session_closer.handler"
  runtime       = "python3.11"

  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256

  timeout     = 60
  memory_size = 512

  environment {
    variables = merge(local.extraction_env, {
      SESSION_IDLE_MINUTES = tostring(var.session_idle_minutes)
    })
  }
}

resource "aws_lambda_function" "dlq_reconciler" {
  function_name = "${var.project_name}-dlq-reconciler"
  role          = aws_iam_role.extraction.arn
  handler       = "src.handlers.dlq_reconciler.handler"
  runtime       = "python3.11"

  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256

  timeout     = 60
  memory_size = 512

  environment {
    variables = merge(local.extraction_env, {
      BATCH_ALERT_TOPIC_ARN = aws_sns_topic.batch_alerts.arn
    })
  }
}

resource "aws_lambda_event_source_mapping" "dlq_reconciler" {
  event_source_arn = aws_sqs_queue.batch_dlq.arn
  function_name    = aws_lambda_function.dlq_reconciler.arn
  batch_size       = 10
}
