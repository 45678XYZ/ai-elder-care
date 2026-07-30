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

# --- RAG（Bedrock Knowledge Base）---

variable "kb_embedding_model_id" {
  description = "Knowledge Base embedding 模型 ID（需先在 Bedrock console 開通 model access）"
  type        = string
  default     = "cohere.embed-multilingual-v3"
}

variable "kb_embedding_dimension" {
  description = "Embedding 向量維度，需與 kb_embedding_model_id 的輸出維度一致"
  type        = number
  default     = 1024
}

variable "kb_chunk_max_tokens" {
  description = "資料來源分塊大小（token 數）"
  type        = number
  default     = 300
}

variable "kb_chunk_overlap_percentage" {
  description = "分塊重疊比例（%）"
  type        = number
  default     = 20
}
