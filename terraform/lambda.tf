# Lambda（Python，程式碼在 backend/src/）
#
# functions：chat / elders / summaries / events / routines / stats / summary_generator
#
# 改用官方認證 Lambda 社群模組 (terraform-aws-modules/lambda/aws) 處理自動打包與依賴安裝。

# --- pre-token-generation trigger：發 ID token 前注入 elder_id claim ---

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

module "pre_token" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-pre-token-trigger"
  description   = "Cognito pre-token-generation trigger"
  handler       = "pre_token_generation.handler"
  runtime       = "python3.11"

  create_role = false
  lambda_role = aws_iam_role.pre_token.arn

  source_path   = "${path.module}/../backend/src/handlers/pre_token_generation.py"
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    ELDER_ACCOUNTS_TABLE = aws_dynamodb_table.elder_accounts.name
  }
}

# 允許此 user pool 呼叫 trigger
resource "aws_lambda_permission" "pre_token_cognito" {
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.pre_token.lambda_function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.accounts.arn
}


# =============================================================================
# 模組 A：chat / tools / elders Lambda 函數宣告與專屬 IAM 權限
# =============================================================================

# 1. 建立模組 A 專屬 Lambda 共用 IAM Role (信任 Lambda 服務)
resource "aws_iam_role" "lambda_backend_role" {
  name = "${var.project_name}-lambda-backend-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# --- 生活記錄（Module B）批次萃取 ---
#
# 多支 Lambda 共用同一個部署包來源（backend/src + backend/requirements.txt），
# 差別只在 handler 與環境變數。

locals {
  # 部署包來源：自動將 backend/src 配至 zip 內的 src/，並由 pip_requirements 自動安裝依賴套件
  backend_source_path = [
    {
      path          = "${path.module}/../backend/src"
      prefix_in_zip = "src"
    },
    {
      path             = "${path.module}/../backend/requirements.txt"
      pip_requirements = true
    }
  ]

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

    # 指標走 EMF（寫 stdout 由 CloudWatch Logs 解析），因此不需要額外的 IAM 權限
    METRICS_NAMESPACE = var.metrics_namespace
    METRICS_ENABLED   = "true"
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

# 2. 基礎 CloudWatch Logs 寫入權限
resource "aws_iam_role_policy_attachment" "lambda_backend_logs" {
  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 3. 模組 A 專屬權限政策 (Polly, S3, Bedrock, SageMaker, SNS, DynamoDB 5 表)
resource "aws_iam_role_policy" "lambda_backend_policy" {
  name = "${var.project_name}-lambda-backend-policy"
  role = aws_iam_role.lambda_backend_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["polly:SynthesizeSpeech"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "arn:aws:s3:::${var.project_name}-audio/*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeAgent", "bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sagemaker:InvokeEndpoint"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish", "sns:Subscribe"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchGetItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.elders.arn,
          aws_dynamodb_table.conversations.arn,
          aws_dynamodb_table.events.arn,
          aws_dynamodb_table.daily_summaries.arn,
          aws_dynamodb_table.routines.arn
        ]
      }
    ]
  })
}

# 照護者即時緊急警報與摘要通知 SNS Topic
resource "aws_sns_topic" "caregiver_notifications" {
  name = "${var.project_name}-caregiver-notifications"
}

# 4. chat Lambda 函數 (POST /chat 對話進入點)
resource "aws_lambda_function" "chat" {
  function_name = "${var.project_name}-chat"
  role          = aws_iam_role.lambda_backend_role.arn
  handler       = "handlers.chat.handler"
  runtime       = "python3.11"
  timeout       = 28
  memory_size   = 512

  filename = "${path.module}/build/backend.zip"

  environment {
    variables = {
      S3_AUDIO_BUCKET            = "${var.project_name}-audio"
      BEDROCK_AGENT_ID           = aws_bedrockagent_agent.elder_companion_agent.id
      BEDROCK_AGENT_ALIAS_ID     = "TSTALIASID"
      AWS_REGION                 = var.aws_region
      SAGEMAKER_CE_ENDPOINT_NAME = ""
      TABLE_ELDERS               = aws_dynamodb_table.elders.name
      TABLE_CONVERSATIONS        = aws_dynamodb_table.conversations.name
      TABLE_EVENTS               = aws_dynamodb_table.events.name
      TABLE_ROUTINES             = aws_dynamodb_table.routines.name
      CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
    }
  }
}

# 5. tools Lambda 函數 (Bedrock Action Group 7 大工具箱)
resource "aws_lambda_function" "tools" {
  function_name = "${var.project_name}-tools"
  role          = aws_iam_role.lambda_backend_role.arn
  handler       = "handlers.tools.handler"
  runtime       = "python3.11"
  timeout       = 15
  memory_size   = 256

  filename = "${path.module}/build/backend.zip"

  environment {
    variables = {
      TABLE_ELDERS               = aws_dynamodb_table.elders.name
      TABLE_CONVERSATIONS        = aws_dynamodb_table.conversations.name
      TABLE_EVENTS               = aws_dynamodb_table.events.name
      TABLE_DAILY_SUMMARIES      = aws_dynamodb_table.daily_summaries.name
      TABLE_ROUTINES             = aws_dynamodb_table.routines.name
      CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
    }
  }
}

# 6. elders Lambda 函數 (GET/POST/PATCH /elders 長者個人檔案與偏好 API)
resource "aws_lambda_function" "elders" {
  function_name = "${var.project_name}-elders"
  role          = aws_iam_role.lambda_backend_role.arn
  handler       = "handlers.elders.handler"
  runtime       = "python3.11"
  timeout       = 10

  filename = "${path.module}/build/backend.zip"

  environment {
    variables = {
      TABLE_ELDERS               = aws_dynamodb_table.elders.name
      CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
    }
  }
}

# =============================================================================
# 5. Cognito Post Confirmation Trigger
# =============================================================================

resource "aws_lambda_function" "post_confirmation" {
  function_name = "${var.project_name}-post-confirmation"
  role          = aws_iam_role.lambda_backend_role.arn
  handler       = "handlers.post_confirmation.handler"
  runtime       = "python3.11"
  timeout       = 10

  filename = "${path.module}/build/backend.zip"

  environment {
    variables = {
      CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
    }
  }
}

resource "aws_lambda_permission" "allow_cognito_post_confirmation" {
  statement_id  = "AllowCognitoInvokePostConfirmation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.post_confirmation.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.accounts.arn
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
data "aws_iam_policy_document" "extraction_data" {
  statement {
    sid    = "EventsWrite"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      # completion event 由 canonical key 穩定推導，因此逐日 occurrence 一律批次取回
      "dynamodb:BatchGetItem",
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
  # 統計只讀 routine 定義：occurrence 狀態由 completion event 推導，統計不回寫 routines
  statement {
    sid     = "RoutinesRead"
    effect  = "Allow"
    actions = ["dynamodb:Query"]
    resources = [
      aws_dynamodb_table.routines.arn,
      "${aws_dynamodb_table.routines.arn}/index/*",
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

module "batch_extractor" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-batch-extractor"
  description   = "Module B 生活記錄批次萃取器"
  handler       = "src.handlers.batch_extractor.handler"
  runtime       = "python3.11"
  timeout       = var.batch_lambda_timeout
  memory_size   = 1024

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    BATCH_LEASE_SECONDS = tostring(var.batch_lambda_timeout * 2)
  })
}

resource "aws_lambda_event_source_mapping" "batch_extractor" {
  event_source_arn                   = aws_sqs_queue.batch.arn
  function_name                      = module.batch_extractor.lambda_function_arn
  batch_size                         = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0
}

module "session_closer" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-session-closer"
  description   = "Session 關閉與離線 materialization 觸發器"
  handler       = "src.handlers.session_closer.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    SESSION_IDLE_MINUTES = tostring(var.session_idle_minutes)
  })
}

module "dlq_reconciler" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-dlq-reconciler"
  description   = "DLQ 訊息對賬與告警器"
  handler       = "src.handlers.dlq_reconciler.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    BATCH_ALERT_TOPIC_ARN = aws_sns_topic.batch_alerts.arn
  })
}

resource "aws_lambda_event_source_mapping" "dlq_reconciler" {
  event_source_arn = aws_sqs_queue.batch_dlq.arn
  function_name    = module.dlq_reconciler.lambda_function_arn
  batch_size       = 10
}

# 照護者端資料 API
module "api_events" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-events"
  description   = "照護者端事件時間軸 API"
  handler       = "src.handlers.events.handler"
  runtime       = "python3.11"
  timeout       = 15
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  environment_variables = local.extraction_env
}

module "api_stats" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-stats"
  description   = "照護者端互動與行程統計 API"
  handler       = "src.handlers.stats.handler"
  runtime       = "python3.11"
  # 統計即時彙總最多一個月的 occurrence 與 completion event，比單頁列表查詢多幾次讀取
  timeout     = 30
  memory_size = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  cloudwatch_logs_retention_in_days = 30

  # ROUTINE_GRACE_MINUTES 讓 routines、摘要與統計共用同一套 missed 判定門檻
  environment_variables = merge(local.extraction_env, {
    ROUTINE_GRACE_MINUTES = tostring(var.routine_grace_minutes)
  })
}

