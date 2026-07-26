variable "project_name" {
  description = "資源命名前綴"
  type        = string
  default     = "ai-elder-care"
}

variable "aws_region" {
  description = "部署區域"
  type        = string
  default     = "us-west-2"
}

variable "health_knowledge_base_id" {
  description = "Bedrock 衛教知識庫 ID (若尚未建立可留空)"
  type        = string
  default     = ""
}
