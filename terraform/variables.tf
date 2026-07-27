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
  description = <<-EOT
    萃取 pipeline 的主對話模型（Converse modelId 或 inference profile）。
    預設走 Anthropic 在 Bedrock 的旗艦模型 + global cross-Region inference profile：
    台灣沒有 Bedrock 區域，global CRIS 的可用性與吞吐優於綁單一區域。
    要固定區域改成 us./apac. 前綴；要省成本改 Sonnet／Haiku。
  EOT
  type        = string
  default     = "global.anthropic.claude-opus-4-6-v1:0"
}

variable "bedrock_classifier_model_id" {
  description = "RAC 分類階段的模型；留空沿用 bedrock_model_id。schema 固定、輸出短，可換便宜模型"
  type        = string
  default     = ""
}

variable "bedrock_extractor_model_id" {
  description = "single-pass 萃取階段的模型；留空沿用 bedrock_model_id。品質瓶頸在這一段，不建議降級"
  type        = string
  default     = ""
}

variable "bedrock_chunker_model_id" {
  description = "llm_prompt 分塊模式的模型；留空沿用 bedrock_model_id"
  type        = string
  default     = ""
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

variable "chunker_type" {
  description = "分塊策略：llm_prompt | embedding_depth | pairwise_v2"
  type        = string
  default     = "llm_prompt"
}

variable "extraction_mode" {
  description = "萃取階段是否啟用硬約束 schema：prompt_guided | structured_output"
  type        = string
  default     = "prompt_guided"
}

variable "rac_top_k" {
  description = "概念檢索回傳的候選節點數"
  type        = number
  default     = 14
}

variable "batch_lambda_timeout" {
  description = "batch extractor 的 timeout（秒）；SQS visibility timeout 由此推導"
  type        = number
  default     = 300
}

variable "session_idle_minutes" {
  description = "active session 閒置多久後由週期性 closer 收斂"
  type        = number
  default     = 30
}

variable "session_sweep_minutes" {
  description = "session sweep 的執行間隔（分鐘）；應短於 batch lease"
  type        = number
  default     = 5
}
