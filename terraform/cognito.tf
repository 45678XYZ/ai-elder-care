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

  # 以 email 當帳號，而不是另外取一個 username。App 的註冊／登入畫面只問 email 與密碼
  # （見 app/lib/shared/services/auth_backend.dart 的介面簽章），沒有輸入 username 的欄位。
  #
  # ⚠️ 這個屬性建立後不可變更，改它會讓整個 user pool 被重建，既有帳號全部消失。
  # 要調整只能趁還沒有人註冊的時候。
  username_attributes = ["email"]

  # 註冊後由 Cognito 寄驗證碼到信箱，App 才會走到驗證碼畫面
  # （SignUpOutcome.needsConfirmation）；沒設的話帳號建了卻永遠是未驗證狀態，登不進去。
  auto_verified_attributes = ["email"]

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
    post_confirmation    = module.post_confirmation.lambda_function_arn
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
