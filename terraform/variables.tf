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

# --- 生活記錄事件萃取（Module B）---

variable "bedrock_model_id" {
  description = "萃取與分類使用的 Bedrock 對話模型（Converse modelId 或 inference profile）"
  type        = string
  default     = "apac.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "embedding_model_id" {
  description = "概念檢索與 turn 切分使用的 embedding 模型"
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dim" {
  description = "embedding 維度；必須與向量索引建立時的維度一致"
  type        = number
  default     = 1024
}

variable "concept_vector_bucket" {
  description = "S3 Vectors vector bucket 名稱（由 build_concept_vector_index.py 建立）"
  type        = string
  default     = "ai-elder-care-vectors"
}

variable "concept_vector_index" {
  description = "概念向量索引名稱；帶模型與維度，換模型即換索引"
  type        = string
  default     = "uco-concepts-titan-v2-1024"
}

variable "event_slot_minutes" {
  description = "canonical event key 的 Slot 粒度（分鐘）"
  type        = number
  default     = 30
}
