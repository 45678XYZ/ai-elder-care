output "cognito_user_pool_id" {
  description = "Cognito user pool ID（App SDK 設定用）"
  value       = aws_cognito_user_pool.accounts.id
}

output "cognito_user_pool_client_id" {
  description = "Cognito app client ID（App SDK 設定用）"
  value       = aws_cognito_user_pool_client.app.id
}

output "asr_ce_endpoint_name" {
  description = "ASR 中文與客語共同備援端點名稱；未啟用時為空字串"
  value       = join("", aws_sagemaker_endpoint.asr_ce[*].name)
}

output "asr_formo_endpoint_names" {
  description = "ASR 客語六腔主力固定 prompt 端點名稱；未啟用時為空 map"
  value       = { for dialect, endpoint in aws_sagemaker_endpoint.asr_formo : dialect => endpoint.name }
}

output "asr_endpoint_instance_types" {
  description = "ASR 七個 SageMaker endpoint 的固定機型；未啟用時為空 map"
  value = var.asr_enable_endpoints ? merge(
    { ce = local.asr_ce_instance_type },
    local.asr_formo_instance_types,
  ) : {}
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

output "tts_endpoint_names" {
  description = "已建立的 TTS endpoint 名稱；未啟用時為空 map"
  value       = { for key, endpoint in aws_sagemaker_endpoint.tts : key => endpoint.name }
}

output "tts_endpoint_instance_types" {
  description = "已建立 TTS endpoint 的逐模型固定機型；未啟用時為空 map"
  value       = { for key, model in local.tts_remote_models : key => model.instance_type }
}

output "tts_config_json" {
  description = "注入 Chat Lambda 的完整 TTS_CONFIG_JSON"
  value       = local.tts_config_json
  sensitive   = false
}

output "agentcore_runtime_arn" {
  description = "對話大腦 Runtime ARN（chat Lambda 環境變數、手動 invoke 測試用）"
  value       = aws_bedrockagentcore_agent_runtime.companion.agent_runtime_arn
}

output "agentcore_memory_id" {
  description = "託管長期記憶 ID；runtime 以此接 AgentCoreMemorySaver"
  value       = aws_bedrockagentcore_memory.companion.id
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


output "summary_generator_function_name" {
  description = "排程摘要 Lambda 名稱；手動補算可用 aws lambda invoke 帶 {\"mode\":\"nightly\",\"date\":\"YYYY-MM-DD\"}"
  value       = module.summary_generator.lambda_function_name
}
