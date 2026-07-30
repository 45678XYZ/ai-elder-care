output "cognito_user_pool_id" {
  description = "Cognito user pool ID（App SDK 設定用）"
  value       = aws_cognito_user_pool.accounts.id
}

output "cognito_user_pool_client_id" {
  description = "Cognito app client ID（App SDK 設定用）"
  value       = aws_cognito_user_pool_client.app.id
}

# 這三個 output 用 join 而非三元運算子取值：count = 0 時 [0] 索引不存在，
# 三元運算子的未取用分支仍會被求值而報錯，join 對空清單則安全回傳空字串。
#
# 注意：這些 output 用於組裝 ASR_CONFIG_JSON（見 asr_lambda_config.tf），
# 不是分別設定 Lambda 的個別 endpoint 環境變數。Lambda 唯一的 ASR 設定來源
# 是 ASR_CONFIG_JSON 一個環境變數。

output "asr_ce_endpoint_name" {
  description = "ASR 主力端點名稱（組裝 ASR_CONFIG_JSON 用）；未啟用時為空字串"
  value       = join("", aws_sagemaker_endpoint.asr_ce[*].name)
}

output "asr_formo_endpoint_name" {
  description = "ASR 客語備援端點名稱（組裝 ASR_CONFIG_JSON 用）；未啟用時為空字串"
  value       = join("", aws_sagemaker_endpoint.asr_formo[*].name)
}

output "asr_invoke_policy_arn" {
  description = "呼叫 ASR 端點的 IAM policy ARN（attach 到 chat Lambda role）；未啟用時為空字串"
  value       = join("", aws_iam_policy.invoke_asr_endpoints[*].arn)
}

output "asr_config_json" {
  description = "完整的 ASR_CONFIG_JSON（注入 chat Lambda 環境變數）；未啟用時為空字串"
  value       = local.asr_config_json
  sensitive   = false
}

# TODO: api_base_url（API Gateway 部署後）
