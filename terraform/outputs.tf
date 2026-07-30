output "cognito_user_pool_id" {
  description = "Cognito user pool ID（App SDK 設定用）"
  value       = aws_cognito_user_pool.accounts.id
}

output "cognito_user_pool_client_id" {
  description = "Cognito app client ID（App SDK 設定用）"
  value       = aws_cognito_user_pool_client.app.id
}

# TODO: api_base_url（API Gateway 部署後）

output "kb_knowledge_base_id" {
  description = "Bedrock Knowledge Base ID（chat Lambda 環境變數、同步腳本用）"
  value       = aws_bedrockagent_knowledge_base.kb.id
}

output "kb_data_source_id" {
  description = "Knowledge Base data source ID（同步腳本觸發 ingestion job 用）"
  value       = aws_bedrockagent_data_source.kb_documents.data_source_id
}

output "kb_documents_bucket" {
  description = "衛教文件 S3 bucket 名稱（同步腳本上傳目標）"
  value       = aws_s3_bucket.kb_documents.bucket
}
