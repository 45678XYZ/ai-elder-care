# ASR／TTS 設定的傳遞方式：SSM Parameter Store。
#
# Lambda 的環境變數總量上限是 4 KB（所有 key 與 value 相加）。六腔客語 ASR 全開時
# ASR 設定約 2.6 KB、兩個 TTS endpoint 全開時 TTS 設定約 2.5 KB，相加必然超過上限，
# apply 會以 RequestEntityTooLargeException 失敗。這不是精簡欄位就能解決的規模問題，
# 因此設定本身放進 SSM，Lambda 環境變數只留參數名稱。
#
# 設定內容是 endpoint 名稱、model ID 與核准旗標，沒有機密，故用 String 而非
# SecureString——省下 KMS 呼叫，也讓 plan 的差異可以直接讀。
#
# Intelligent-Tiering：目前兩份都在 Standard 的 4 KB 內（免費），日後成長超過時由 AWS
# 自動升級為 Advanced，不必再改一次 Terraform 才能 apply。

resource "aws_ssm_parameter" "asr_config" {
  name        = "/${var.project_name}/asr/config"
  description = "Chat Lambda 的 ASR route／provider／gate 設定（見 asr_lambda_config.tf）"
  type        = "String"
  tier        = "Intelligent-Tiering"
  value       = local.asr_config_json
}

resource "aws_ssm_parameter" "tts_config" {
  name        = "/${var.project_name}/tts/config"
  description = "Chat Lambda 的 TTS route／provider／gate 設定（見 tts_lambda_config.tf）"
  type        = "String"
  tier        = "Intelligent-Tiering"
  value       = local.tts_config_json
}

# Chat Lambda 在 cold start 讀這兩個參數。讀取失敗時 composition 會 fail closed，
# 因此權限缺失會表現為明確錯誤，而不是安靜地退回預設設定。
resource "aws_iam_policy" "read_lambda_config_parameters" {
  name        = "${var.project_name}-read-lambda-config-parameters"
  description = "允許 Chat Lambda 讀取 ASR／TTS 設定參數"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "ssm:GetParameter"
      Resource = [
        aws_ssm_parameter.asr_config.arn,
        aws_ssm_parameter.tts_config.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "chat_read_config_parameters" {
  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = aws_iam_policy.read_lambda_config_parameters.arn
}
