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
  default     = 10
}

variable "session_sweep_minutes" {
  description = "session sweep 的執行間隔（分鐘）；應短於 batch lease"
  type        = number
  default     = 5
}

variable "api_throttle_rate_limit" {
  description = "API Gateway stage 的每秒請求上限（防呆與成本上限，非效能調校）"
  type        = number
  default     = 50
}

variable "api_throttle_burst_limit" {
  description = "API Gateway stage 的突發請求上限"
  type        = number
  default     = 100
}

variable "metrics_namespace" {
  description = "EMF 指標的 CloudWatch namespace"
  type        = string
  default     = "AiElderCare/Extraction"
}

# --- 每日摘要（Module B，見 docs/feature_daily-summarization.md）---

variable "summary_generator_version" {
  description = "寫入 daily_summaries.generator_version 的版本戳記"
  type        = string
  default     = "summary-generator-1"
}

variable "bedrock_summary_model_id" {
  description = "摘要生成階段的模型；留空沿用 bedrock_model_id"
  type        = string
  default     = ""
}

variable "summary_alert_lookback_days" {
  description = "alerts 判斷跨日趨勢時回看的天數（含當日）；設 1 等於停用跨日線索"
  type        = number
  default     = 7
}

variable "summary_max_events" {
  description = "進 prompt 的當日事件數上限；防萃取異常時 prompt 無上限成長"
  type        = number
  default     = 120
}

variable "summary_wait_minutes" {
  description = <<-EOT
    partial 摘要的重算等待窗口（分鐘）。超過就停止重算：batch 卡在 failed 是 DLQ
    reconciler 與告警的責任，不該讓摘要無限重算燒模型費用。
  EOT
  type        = number
  default     = 180
}

variable "summary_nightly_cron" {
  description = <<-EOT
    每晚生成當日摘要的排程。EventBridge cron 一律 UTC，因此台灣時間要自己減 8 小時：
    預設 15:50 UTC = 23:50+08:00，排在日界前讓照護者當晚就看得到當天摘要。
  EOT
  type        = string
  default     = "cron(50 15 * * ? *)"
}

variable "summary_backfill_days" {
  description = "backfill sweep 回看幾天的摘要（含當日）"
  type        = number
  default     = 2
}

variable "summary_backfill_minutes" {
  description = "backfill sweep 的執行間隔（分鐘）；必須短於 summary_wait_minutes"
  type        = number
  default     = 30
}

variable "summary_sweep_limit" {
  description = "單次 sweep 處理的長者數上限，避免 Lambda 超時"
  type        = number
  default     = 50
}

variable "routine_grace_minutes" {
  description = "occurrence 由 pending 轉 missed 的寬限（分鐘）；routines、摘要與統計共用"
  type        = number
  default     = 120
}

variable "summary_lambda_timeout" {
  description = "api_summaries 的 timeout（秒）；POST /summaries/generate 會同步呼叫模型"
  type        = number
  default     = 60
}

variable "summary_generator_timeout" {
  description = "排程 summary_generator 的 timeout（秒）；一次跑多位長者"
  type        = number
  default     = 600
}

variable "summary_partial_alarm_threshold" {
  description = "每小時 partial 摘要數超過此值且連續三小時即告警（batch 可能卡住）"
  type        = number
  default     = 10
}
