# Cognito（長者／照護者帳號與角色）
#
# 註冊／登入由 App 直接走 Cognito SDK，不經 API Gateway；ID token 交由 API Gateway
# 的 Cognito authorizer 驗證。
#
# 長者身分（elder_id）不存在 Cognito，而是在發 token 前由 pre-token-generation
# trigger 依 sub 查對應表注入 elder_id claim（見 lambda.tf、dynamodb.tf 的
# elder_accounts 表、backend/src/handlers/pre_token_generation.py）。角色不另設群組：
# 後端以「有無 elder_id claim」判定長者／照護者（見 backend/src/shared/auth.py）。

resource "aws_cognito_user_pool" "accounts" {
  name = "${var.project_name}-users"

  # 長者多為長輩自行登入，密碼規則從寬但仍要求最低長度與基本複雜度
  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_uppercase = false
    require_symbols   = false
  }

  # 發 ID token 前呼叫 trigger 注入 elder_id claim
  # 註冊完成後自動綁定 SNS
  lambda_config {
    pre_token_generation = module.pre_token.lambda_function_arn
    post_confirmation    = aws_lambda_function.post_confirmation.arn
  }
}

resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.project_name}-app"
  user_pool_id = aws_cognito_user_pool.accounts.id

  # 公開行動端（Flutter）無法保管密鑰，不產生 client secret
  generate_secret = false

  # 密碼走 SRP，避免會傳明文密碼的 USER_PASSWORD_AUTH
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}
