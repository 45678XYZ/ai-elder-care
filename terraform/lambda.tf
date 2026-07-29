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
        Action   = ["sns:Publish"]
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
  timeout       = 30
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
      TABLE_ELDERS = aws_dynamodb_table.elders.name
    }
  }
}
