output "cognito_user_pool_id" {
  description = "Cognito user pool ID（App SDK 設定用）"
  value       = aws_cognito_user_pool.accounts.id
}

output "cognito_user_pool_client_id" {
  description = "Cognito app client ID（App SDK 設定用）"
  value       = aws_cognito_user_pool_client.app.id
}

output "api_base_url" {
  description = "API 基底位址（App SDK 設定用）；所有端點掛在此前綴下"
  value       = aws_api_gateway_stage.v1.invoke_url
}

output "batch_queue_url" {
  description = "batch 派送佇列 URL；session closer 送訊息、batch extractor 消費"
  value       = aws_sqs_queue.batch.id
}

output "concept_vector_index" {
  description = "概念向量索引名稱；填索引內容時傳給 build_concept_vector_index.py"
  value       = aws_s3vectors_index.concepts.index_name
}
