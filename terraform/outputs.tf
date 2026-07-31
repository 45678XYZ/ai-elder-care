output "cognito_user_pool_id" {
  description = "Cognito user pool ID（App SDK 設定用）"
  value       = aws_cognito_user_pool.accounts.id
}

output "cognito_user_pool_client_id" {
  description = "Cognito app client ID（App SDK 設定用）"
  value       = aws_cognito_user_pool_client.app.id
}

output "asr_ce_endpoint_name" {
  description = "ASR 主力端點名稱；未啟用時為空字串"
  value       = join("", aws_sagemaker_endpoint.asr_ce[*].name)
}

output "asr_formo_endpoint_name" {
  description = "ASR 客語備援端點名稱；未啟用時為空字串"
  value       = join("", aws_sagemaker_endpoint.asr_formo[*].name)
}

output "asr_invoke_policy_arn" {
  description = "Chat Lambda 的 ASR endpoint 呼叫 policy ARN；未啟用時為空字串"
  value       = join("", aws_iam_policy.invoke_asr_endpoints[*].arn)
}

output "asr_config_json" {
  description = "注入 Chat Lambda 的完整 ASR_CONFIG_JSON"
  value       = local.asr_config_json
  sensitive   = false
}

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

output "summary_generator_function_name" {
  description = "排程摘要 Lambda 名稱；手動補算可用 aws lambda invoke 帶 {\"mode\":\"nightly\",\"date\":\"YYYY-MM-DD\"}"
  value       = module.summary_generator.lambda_function_name
}
