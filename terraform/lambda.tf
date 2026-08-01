# Lambda（Python，程式碼在 backend/src/）
#
# functions：chat / elders / summaries / events / routines / stats / summary_generator
#
# 改用官方認證 Lambda 社群模組 (terraform-aws-modules/lambda/aws) 處理自動打包與依賴安裝。

# --- pre-token-generation trigger：發 ID token 前注入 elder_id claim ---

resource "aws_iam_role" "pre_token" {
  name = "${var.project_name}-pre-token-trigger"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "pre_token_logs" {
  role       = aws_iam_role.pre_token.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 最小權限：只讀對應表查 sub→elder_id
resource "aws_iam_role_policy" "pre_token_ddb" {
  name = "read-elder-accounts"
  role = aws_iam_role.pre_token.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "dynamodb:GetItem"
      Resource = aws_dynamodb_table.elder_accounts.arn
    }]
  })
}

module "pre_token" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-pre-token-trigger"
  description   = "Cognito pre-token-generation trigger"
  handler       = "pre_token_generation.handler"
  runtime       = "python3.13"

  create_role = false
  lambda_role = aws_iam_role.pre_token.arn

  source_path   = "${path.module}/../backend/src/handlers/pre_token_generation.py"
  artifacts_dir = "${path.module}/build"

  # 單檔且不裝任何依賴，因此不必進 Docker 打包；架構仍與其餘 Lambda 一致
  architectures = local.lambda_architectures

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    ELDER_ACCOUNTS_TABLE = aws_dynamodb_table.elder_accounts.name
  }
}

# 允許此 user pool 呼叫 trigger
resource "aws_lambda_permission" "pre_token_cognito" {
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.pre_token.lambda_function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.accounts.arn
}


# =============================================================================
# 模組 A：chat / tools / elders Lambda 函數宣告與專屬 IAM 權限
# =============================================================================

# 1. 建立模組 A 專屬 Lambda 共用 IAM Role (信任 Lambda 服務)
resource "aws_iam_role" "lambda_backend_role" {
  name = "${var.project_name}-lambda-backend-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# --- 生活記錄（Module B）批次萃取 ---
#
# 多支 Lambda 共用同一個部署包來源（backend/src + backend/requirements.txt），
# 差別只在 handler 與環境變數。

locals {
  # 部署包來源：自動將 backend/src 配至 zip 內的 src/，並由 pip_requirements 自動安裝依賴套件
  backend_source_path = [
    {
      path          = "${path.module}/../backend/src"
      prefix_in_zip = "src"
    },
    {
      path             = "${path.module}/../backend/requirements.txt"
      pip_requirements = true
    }
  ]

  # 執行架構統一 arm64。AgentCore Runtime 只支援 arm64，且會逐一驗證部署包內 .so 的 ELF
  # header，混到 x86_64 或 macOS 的 Mach-O 會直接被拒；Lambda 跟著一起走 arm64，打包平台
  # 就只剩一種，在 Apple Silicon 上也不必靠模擬跑 pip。
  lambda_architectures = ["arm64"]

  # 依賴一律在容器內安裝。社群模組的 package.py 是直接在本機跑 pip 且不帶 --platform，
  # 在 macOS 上會裝成 macosx wheel（requirements.txt 的 pydantic 帶編譯出來的 pydantic-core），
  # zip 上去後 Lambda 冷啟就 ImportModuleError。映像檔由模組依 runtime 推導
  # （public.ecr.aws/sam/build-<runtime>），這裡只指定平台，讓誰打包結果都一樣。
  docker_build_options = ["--platform", "linux/arm64"]

  # runtime 必須是 python3.12 以上，不能退回 3.11。
  #
  # python3.11 的執行環境是 Amazon Linux 2（glibc 2.26），但 requirements.txt 的
  # av 只出 manylinux_2_28、numpy 只出 manylinux_2_27 的 arm64 wheel，兩個都裝不上去。
  # pip 裝不了 wheel 會退回編譯原始碼，而 PyAV 需要 FFmpeg 的開發標頭檔，SAM 映像檔裡
  # 沒有，打包會直接失敗（pkg-config could not find libraries ['avformat', ...]）。
  # python3.13 的環境是 Amazon Linux 2023（glibc 2.34），三個套件都裝得起來。

  # 萃取行為一律由環境變數驅動，程式不寫死（見 docs/framework.md 後端環境變數）
  extraction_env = {
    TABLE_ELDERS          = aws_dynamodb_table.elders.name
    TABLE_CONVERSATIONS   = aws_dynamodb_table.conversations.name
    TABLE_EVENTS          = aws_dynamodb_table.events.name
    TABLE_ROUTINES        = aws_dynamodb_table.routines.name
    TABLE_DAILY_SUMMARIES = aws_dynamodb_table.daily_summaries.name

    BEDROCK_MODEL_ID            = var.bedrock_model_id
    BEDROCK_CLASSIFIER_MODEL_ID = var.bedrock_classifier_model_id
    BEDROCK_EXTRACTOR_MODEL_ID  = var.bedrock_extractor_model_id
    BEDROCK_CHUNKER_MODEL_ID    = var.bedrock_chunker_model_id

    EMBEDDING_MODEL_ID    = var.embedding_model_id
    EMBEDDING_DIM         = tostring(var.embedding_dim)
    CONCEPT_VECTOR_BUCKET = var.concept_vector_bucket
    CONCEPT_VECTOR_INDEX  = var.concept_vector_index

    EVENT_SLOT_MINUTES = tostring(var.event_slot_minutes)
    CHUNKER_TYPE       = var.chunker_type
    EXTRACTION_MODE    = var.extraction_mode
    RAC_TOP_K          = tostring(var.rac_top_k)

    BATCH_QUEUE_URL = aws_sqs_queue.batch.id

    # 指標走 EMF（寫 stdout 由 CloudWatch Logs 解析），因此不需要額外的 IAM 權限
    METRICS_NAMESPACE = var.metrics_namespace
    METRICS_ENABLED   = "true"
  }
}

resource "aws_iam_role" "extraction" {
  name = "${var.project_name}-extraction"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# 2. 基礎 CloudWatch Logs 寫入權限
resource "aws_iam_role_policy_attachment" "lambda_backend_logs" {
  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 3. 模組 A 專屬權限政策 (Polly, S3, Bedrock, SageMaker, SNS, DynamoDB 5 表)
resource "aws_iam_role_policy" "lambda_backend_policy" {
  name = "${var.project_name}-lambda-backend-policy"
  role = aws_iam_role.lambda_backend_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["polly:SynthesizeSpeech"]
        Resource = "*"
      },
      {
        # Amazon Transcribe Streaming 不支援資源層級 ARN 限制。
        Effect   = "Allow"
        Action   = ["transcribe:StartStreamTranscription"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "arn:aws:s3:::${var.project_name}-audio/*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime", "bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish", "sns:Subscribe"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchGetItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.elders.arn,
          aws_dynamodb_table.conversations.arn,
          aws_dynamodb_table.events.arn,
          aws_dynamodb_table.daily_summaries.arn,
          aws_dynamodb_table.routines.arn
        ]
      }
    ]
  })
}

# 照護者即時緊急警報與摘要通知 SNS Topic
resource "aws_sns_topic" "caregiver_notifications" {
  name = "${var.project_name}-caregiver-notifications"
}

# 4. chat Lambda 函數 (POST /chat 對話進入點)
#
# AWS_REGION 是 Lambda 的保留環境變數（由執行環境自動注入），設了會讓 CreateFunction
# 直接被拒；chat.py 讀的就是那個注入值，因此這裡不重複宣告。
module "chat" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-chat"
  description   = "POST /chat 對話進入點"
  handler       = "src.handlers.chat.handler"
  runtime       = "python3.13"
  timeout       = 28
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.lambda_backend_role.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    S3_AUDIO_BUCKET = "${var.project_name}-audio"

    # 對話大腦：AgentCore Runtime 以 ARN 定址、以 endpoint 名稱當 qualifier（見 agentcore.tf）
    AGENTCORE_RUNTIME_ARN   = aws_bedrockagentcore_agent_runtime.companion.agent_runtime_arn
    AGENTCORE_ENDPOINT_NAME = aws_bedrockagentcore_agent_runtime_endpoint.live.name

    # ASR／TTS 的路由、核准與 endpoint 都由這兩份設定驅動（見 asr_lambda_config.tf、tts_lambda_config.tf）
    ASR_CONFIG_JSON = local.asr_config_json
    TTS_CONFIG_JSON = local.tts_config_json

    TABLE_ELDERS               = aws_dynamodb_table.elders.name
    TABLE_CONVERSATIONS        = aws_dynamodb_table.conversations.name
    TABLE_EVENTS               = aws_dynamodb_table.events.name
    TABLE_ROUTINES             = aws_dynamodb_table.routines.name
    CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn

    # turn 狀態機：租約長度必須大於本函數的 timeout，否則同一個請求還在跑就會被判定為
    # 「前一個 invocation 已死」而被接管，副作用會做第二次
    REQUEST_LEASE_SECONDS      = tostring(var.request_lease_seconds)
    SESSION_IDLE_MINUTES       = tostring(var.session_idle_minutes)
    SESSION_MAX_TURNS          = tostring(var.session_max_turns)
    SESSION_MAX_INFLIGHT_TURNS = tostring(var.session_max_inflight_turns)
    SESSION_MAX_INPUT_BYTES    = tostring(var.session_max_input_bytes)
  }
}

# 5. tools Lambda 函數（12 個工具箱；由 AgentCore Runtime 以 lambda:InvokeFunction 呼叫）
module "tools" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-tools"
  description   = "對話大腦的工具箱"
  handler       = "src.handlers.tools.handler"
  runtime       = "python3.13"
  timeout       = 15
  memory_size   = 256

  create_role = false
  lambda_role = aws_iam_role.lambda_backend_role.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    TABLE_ELDERS               = aws_dynamodb_table.elders.name
    TABLE_CONVERSATIONS        = aws_dynamodb_table.conversations.name
    TABLE_EVENTS               = aws_dynamodb_table.events.name
    TABLE_DAILY_SUMMARIES      = aws_dynamodb_table.daily_summaries.name
    TABLE_ROUTINES             = aws_dynamodb_table.routines.name
    CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
  }
}

# 6. elders Lambda 函數 (GET/POST/PATCH /elders 長者個人檔案與偏好 API)
module "elders" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-elders"
  description   = "長者個人檔案與偏好 API"
  handler       = "src.handlers.elders.handler"
  runtime       = "python3.13"
  timeout       = 10
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.lambda_backend_role.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    TABLE_ELDERS               = aws_dynamodb_table.elders.name
    CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
  }
}


# =============================================================================
# 5. Cognito Post Confirmation Trigger
# =============================================================================

module "post_confirmation" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-post-confirmation"
  description   = "Cognito post-confirmation trigger：註冊完成後綁定 SNS"
  handler       = "src.handlers.post_confirmation.handler"
  runtime       = "python3.13"
  timeout       = 10

  create_role = false
  lambda_role = aws_iam_role.lambda_backend_role.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
  }
}

resource "aws_lambda_permission" "allow_cognito_post_confirmation" {
  statement_id  = "AllowCognitoInvokePostConfirmation"
  action        = "lambda:InvokeFunction"
  function_name = module.post_confirmation.lambda_function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.accounts.arn
}

resource "aws_iam_role_policy_attachment" "extraction_logs" {
  role       = aws_iam_role.extraction.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "extraction_bedrock" {
  role       = aws_iam_role.extraction.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}

resource "aws_iam_role_policy_attachment" "extraction_vectors" {
  role       = aws_iam_role.extraction.name
  policy_arn = aws_iam_policy.concept_vector_read.arn
}

# 最小權限：只給實際會碰到的表與動作。
data "aws_iam_policy_document" "extraction_data" {
  statement {
    sid    = "EventsWrite"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      # completion event 由 canonical key 穩定推導，因此逐日 occurrence 一律批次取回
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.events.arn,
      "${aws_dynamodb_table.events.arn}/index/*",
    ]
  }
  statement {
    sid    = "ConversationsSessionState"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.conversations.arn,
      "${aws_dynamodb_table.conversations.arn}/index/*",
    ]
  }

  statement {
    sid    = "EldersRead"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Scan",
    ]

    resources = [aws_dynamodb_table.elders.arn]
  }

  # 摘要：條件式覆寫需要 PutItem，列表與重算 sweep 需要 Query／GetItem
  statement {
    sid    = "DailySummariesWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]

    resources = [aws_dynamodb_table.daily_summaries.arn]
  }

  # occurrence 衍生只讀：版本清單走 routine-versions-by-elder，完成當時的定義走 GetItem。
  # 統計、摘要與事件 API 共用這一條——occurrence 狀態一律由 completion event 推導，
  # 這個角色不回寫 routines（寫入路徑見 aws_iam_role.api_routines）。
  # 同一份 policy document 內 Sid 必須唯一，因此這裡只留一條 routines 讀取權限。
  statement {
    sid    = "RoutinesRead"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.routines.arn,
      "${aws_dynamodb_table.routines.arn}/index/*",
    ]
  }

  statement {
    sid    = "BatchQueue"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.batch.arn, aws_sqs_queue.batch_dlq.arn]
  }
  statement {
    sid       = "BatchAlerts"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.batch_alerts.arn]
  }
}

resource "aws_iam_role_policy" "extraction_data" {
  name   = "extraction-data-access"
  role   = aws_iam_role.extraction.id
  policy = data.aws_iam_policy_document.extraction_data.json
}

resource "aws_sns_topic" "batch_alerts" {
  name = "${var.project_name}-batch-alerts"
}

module "batch_extractor" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-batch-extractor"
  description   = "Module B 生活記錄批次萃取器"
  handler       = "src.handlers.batch_extractor.handler"
  runtime       = "python3.13"
  timeout       = var.batch_lambda_timeout
  memory_size   = 1024

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    BATCH_LEASE_SECONDS = tostring(var.batch_lambda_timeout * 2)
  })
}

resource "aws_lambda_event_source_mapping" "batch_extractor" {
  event_source_arn                   = aws_sqs_queue.batch.arn
  function_name                      = module.batch_extractor.lambda_function_arn
  batch_size                         = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0
}

module "session_closer" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-session-closer"
  description   = "Session 關閉與離線 materialization 觸發器"
  handler       = "src.handlers.session_closer.handler"
  runtime       = "python3.13"
  timeout       = 60
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    SESSION_IDLE_MINUTES = tostring(var.session_idle_minutes)
  })
}

module "dlq_reconciler" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-dlq-reconciler"
  description   = "DLQ 訊息對賬與告警器"
  handler       = "src.handlers.dlq_reconciler.handler"
  runtime       = "python3.13"
  timeout       = 60
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, {
    BATCH_ALERT_TOPIC_ARN = aws_sns_topic.batch_alerts.arn
  })
}

resource "aws_lambda_event_source_mapping" "dlq_reconciler" {
  event_source_arn = aws_sqs_queue.batch_dlq.arn
  function_name    = module.dlq_reconciler.lambda_function_arn
  batch_size       = 10
}

# 照護者端資料 API
module "api_events" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-events"
  description   = "照護者端事件時間軸 API"
  handler       = "src.handlers.events.handler"
  runtime       = "python3.13"
  timeout       = 15
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = local.extraction_env
}

# --- 每日摘要（見 docs/feature_daily-summarization.md）---


# 摘要相關 Lambda 共用的環境變數；模型呼叫與等待窗口都可調，不寫死在程式碼
locals {
  summary_env = {
    SUMMARY_GENERATOR_VERSION   = var.summary_generator_version
    BEDROCK_SUMMARY_MODEL_ID    = var.bedrock_summary_model_id
    SUMMARY_ALERT_LOOKBACK_DAYS = tostring(var.summary_alert_lookback_days)
    SUMMARY_MAX_EVENTS          = tostring(var.summary_max_events)
    SUMMARY_WAIT_MINUTES        = tostring(var.summary_wait_minutes)
    SUMMARY_BACKFILL_DAYS       = tostring(var.summary_backfill_days)
    SUMMARY_SWEEP_LIMIT         = tostring(var.summary_sweep_limit)
    ROUTINE_GRACE_MINUTES       = tostring(var.routine_grace_minutes)
  }
}

# GET /summaries 與 POST /summaries/generate 同一支 handler（依 httpMethod 分派）。
# POST 會同步呼叫模型，因此 timeout 比純查詢的 api_events 長。
module "api_summaries" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-summaries"
  description   = "照護者端每日摘要 API"
  handler       = "src.handlers.summaries.handler"
  runtime       = "python3.13"
  timeout       = var.summary_lambda_timeout
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, local.summary_env)
}

# 排程生成：nightly 與 backfill 兩種 mode 由 EventBridge 的 input 指定
module "summary_generator" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-summary-generator"
  description   = "排程每日摘要生成器（nightly + backfill）"
  handler       = "src.handlers.summary_generator.handler"
  runtime       = "python3.13"
  timeout       = var.summary_generator_timeout
  memory_size   = 1024

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = merge(local.extraction_env, local.summary_env)
}

module "api_stats" {

  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-stats"
  description   = "照護者端互動與行程統計 API"
  handler       = "src.handlers.stats.handler"
  runtime       = "python3.13"
  # 統計即時彙總最多一個月的 occurrence 與 completion event，比單頁列表查詢多幾次讀取
  timeout     = 30
  memory_size = 512

  create_role = false
  lambda_role = aws_iam_role.extraction.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  # ROUTINE_GRACE_MINUTES 讓 routines、摘要與統計共用同一套 missed 判定門檻
  environment_variables = merge(local.extraction_env, {
    ROUTINE_GRACE_MINUTES = tostring(var.routine_grace_minutes)
  })
}

# --- 例行公事 API（規格見 docs/api.md「例行公事」）---
#
# 這是唯一會寫 routines 表的路徑，因此不共用 aws_iam_role.extraction：那個角色刻意只給
# routines 讀取權，讓萃取與統計不可能回寫定義。

resource "aws_iam_role" "api_routines" {
  name = "${var.project_name}-api-routines"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_routines_logs" {
  role       = aws_iam_role.api_routines.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "api_routines_data" {
  # 版本不可變：建立走 PutItem，改版走 transact_write_items（關閉舊 current + 寫新版），
  # 因此需要 PutItem 與 UpdateItem——transaction 在 IAM 上檢查的是底層那兩個動作
  statement {
    sid    = "RoutinesWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.routines.arn,
      "${aws_dynamodb_table.routines.arn}/index/*",
    ]
  }

  # 手動完成寫 canonical completion event；當日行程視圖以 BatchGetItem 反查完成狀態
  statement {
    sid    = "CompletionEvents"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.events.arn,
      "${aws_dynamodb_table.events.arn}/index/*",
    ]
  }

  # assert_can_access_elder 依 elders.caregiver_ids 判定越權（見 backend/src/shared/auth.py）
  statement {
    sid       = "EldersRead"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.elders.arn]
  }
}

resource "aws_iam_role_policy" "api_routines_data" {
  name   = "api-routines-data-access"
  role   = aws_iam_role.api_routines.id
  policy = data.aws_iam_policy_document.api_routines_data.json
}

# 四條路由（列表／當日／建立／改版／手動完成）掛同一支 handler，依 httpMethod 與 resource 分派
module "api_routines" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-api-routines"
  description   = "例行公事定義與當日行程 API"
  handler       = "src.handlers.routines.handler"
  runtime       = "python3.13"
  timeout       = 15
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.api_routines.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.lambda_artifacts.id
  s3_prefix   = "backend/"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    TABLE_ELDERS   = aws_dynamodb_table.elders.name
    TABLE_EVENTS   = aws_dynamodb_table.events.name
    TABLE_ROUTINES = aws_dynamodb_table.routines.name

    ROUTINE_GRACE_MINUTES = tostring(var.routine_grace_minutes)

    METRICS_NAMESPACE = var.metrics_namespace
    METRICS_ENABLED   = "true"
  }
}
