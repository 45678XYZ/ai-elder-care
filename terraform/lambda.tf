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
