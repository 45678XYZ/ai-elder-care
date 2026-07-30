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

# --- ASR 實體模型端點（見 asr_models.tf）---
#
# 預設 false：不建立任何 GPU 資源。這與後端 ASR 模組的 model production gate
# 一致——模型未經人工核准前，程式層與基礎設施層都保持關閉。
#
# 啟用前置條件（fail-closed）：
# 1. 兩個模型的 image URI 與 model-data URL 都必須提供
# 2. artifact bucket 必須存在
# 3. Formo 的方言 prompt 必須在 container deployment 中固定（Lambda 不傳送）
# 4. 程式側 model production gate 5 項全部核准

variable "asr_enable_endpoints" {
  description = "是否建立 ASR 推論端點。預設關閉，避免未驗證的模型產生 GPU 費用"
  type        = bool
  default     = false
}

variable "asr_model_artifact_bucket" {
  description = "存放 ASR 模型 artifact（model.tar.gz）的 S3 bucket 名稱"
  type        = string
  default     = ""
}

variable "asr_ce_image_uri" {
  description = "Taiwan-Tongues-ASR-CE 推論容器的 ECR image URI"
  type        = string
  default     = ""
}

variable "asr_ce_model_data_url" {
  description = "Taiwan-Tongues-ASR-CE 模型 artifact 的 S3 URI"
  type        = string
  default     = ""
}

variable "asr_ce_instance_type" {
  description = "CE 端點的推論機型。CE 為 whisper-large-v2 微調（CTranslate2），需 GPU"
  type        = string
  default     = "ml.g5.xlarge"
}

variable "asr_ce_min_instances" {
  description = "CE 端點最小實例數"
  type        = number
  default     = 1
}

variable "asr_ce_max_instances" {
  description = "CE 端點最大實例數（依流量自動擴充上限）"
  type        = number
  default     = 4
}

variable "asr_formo_image_uri" {
  description = "FormoSpeech Whisper-v3 推論容器的 ECR image URI"
  type        = string
  default     = ""
}

variable "asr_formo_model_data_url" {
  description = "FormoSpeech Whisper-v3 模型 artifact 的 S3 URI"
  type        = string
  default     = ""
}

variable "asr_formo_instance_type" {
  description = "Formo 端點的推論機型。Formo 約 20 億參數，記憶體需求高於 CE"
  type        = string
  default     = "ml.g5.2xlarge"
}

variable "asr_formo_min_instances" {
  description = "Formo 端點最小實例數"
  type        = number
  default     = 1
}

variable "asr_formo_max_instances" {
  description = "Formo 端點最大實例數"
  type        = number
  default     = 2
}

variable "asr_target_invocations_per_instance" {
  description = "每個實例的目標每分鐘呼叫數；超過即向外擴充"
  type        = number
  default     = 20
}
