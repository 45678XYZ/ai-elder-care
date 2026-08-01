# EventBridge
#   - 每晚觸發 summary_generator（台灣時間）
#   - 週期性重算仍為 partial 的摘要（等待窗口內）
#   - 週期性 session closer：idle close、BATCH#PENDING 補投、BATCH#PROCESSING lease 過期重投
# EventBridge Scheduler：每晚 22:00 (台灣時間 UTC+8 = UTC 14:00) 觸發照護者晚報
#
# 架構說明：
# EventBridge Cron Rule → 觸發 daily_digest Lambda
# daily_digest Lambda → 為每位長者查詢 daily_summaries 表 + get_daily_routines
#                      → 呼叫 SNS publish 發送照護者晚報 Email
#
# 台灣時間 22:00 = UTC 14:00 → cron(0 14 * * ? *)

# =============================================================================
# 1. daily_digest Lambda 專屬 IAM Role
# =============================================================================

resource "aws_iam_role" "daily_digest_role" {
  name = "${var.project_name}-daily-digest-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "daily_digest_logs" {
  role       = aws_iam_role.daily_digest_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "daily_digest_policy" {
  name = "${var.project_name}-daily-digest-policy"
  role = aws_iam_role.daily_digest_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 讀取長者列表與行程完成狀態
        Effect = "Allow"
        Action = [
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:GetItem"
        ]
        Resource = [
          aws_dynamodb_table.elders.arn,
          aws_dynamodb_table.routines.arn,
          "${aws_dynamodb_table.routines.arn}/index/*",
          aws_dynamodb_table.events.arn,
          "${aws_dynamodb_table.events.arn}/index/*",
          aws_dynamodb_table.daily_summaries.arn
        ]
      },
      {
        # 發送 SNS 通知給照護者
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.caregiver_notifications.arn
      }
    ]
  })
}

# =============================================================================
# 2. daily_digest Lambda 函數（每晚晚報彙整與推播）
# =============================================================================

module "daily_digest" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-daily-digest"
  description   = "每晚照護者晚報彙整與推播"
  handler       = "src.handlers.daily_digest.handler"
  runtime       = "python3.11"
  timeout       = 120 # 晚報需掃描所有長者，給足夠時間
  memory_size   = 256

  create_role = false
  lambda_role = aws_iam_role.daily_digest_role.arn

  source_path   = local.backend_source_path
  artifacts_dir = "${path.module}/build"

  architectures             = local.lambda_architectures
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    TABLE_ELDERS               = aws_dynamodb_table.elders.name
    TABLE_ROUTINES             = aws_dynamodb_table.routines.name
    TABLE_EVENTS               = aws_dynamodb_table.events.name
    TABLE_DAILY_SUMMARIES      = aws_dynamodb_table.daily_summaries.name
    CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn

    # AWS_REGION 是 Lambda 保留字，不能自己設；daily_digest.py 因此讀 AWS_REGION_NAME
    AWS_REGION_NAME = var.aws_region
  }
}

# =============================================================================
# 3. EventBridge Scheduler Role（允許 EventBridge 呼叫 Lambda）
# =============================================================================

resource "aws_iam_role" "eventbridge_scheduler_role" {
  name = "${var.project_name}-eventbridge-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_invoke_lambda" {
  name = "${var.project_name}-eventbridge-invoke-policy"
  role = aws_iam_role.eventbridge_scheduler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = module.daily_digest.lambda_function_arn
    }]
  })
}

# =============================================================================
# 4. EventBridge Scheduler：每晚 22:00 台灣時間（UTC 14:00）觸發晚報
# =============================================================================

resource "aws_scheduler_schedule" "daily_digest_schedule" {
  name       = "${var.project_name}-daily-digest-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF" # 嚴格固定時間，不允許彈性窗口
  }

  # cron(分 時 日 月 星期 年) — UTC 14:00 = 台灣時間 22:00
  schedule_expression          = "cron(0 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = module.daily_digest.lambda_function_arn
    role_arn = aws_iam_role.eventbridge_scheduler_role.arn

    # 傳給 Lambda 的事件 payload（空 JSON 即可，Lambda 自行查詢所有長者）
    input = jsonencode({
      source            = "aws.scheduler"
      task_type         = "daily_digest"
      trigger_time_utc8 = "22:00"
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}

# Lambda permission：允許 EventBridge Scheduler 呼叫 daily_digest
resource "aws_lambda_permission" "allow_scheduler_invoke_daily_digest" {
  statement_id  = "AllowEventBridgeSchedulerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.daily_digest.lambda_function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.daily_digest_schedule.arn
}

# --- 每日摘要（見 docs/feature_daily-summarization.md §7）---

# cron 一律 UTC，因此台灣時間要自己減 8 小時；預設 23:50+08:00 → 15:50 UTC。
# 排在日界前而不是隔天，是為了讓照護者當晚就看得到當天的摘要；仍有未完成 batch 時寫
# partial，後續由 backfill 重算補成 complete。
resource "aws_cloudwatch_event_rule" "summary_nightly" {
  name                = "${var.project_name}-summary-nightly"
  description         = "每晚生成當日摘要（台灣時間）"
  schedule_expression = var.summary_nightly_cron
}

resource "aws_cloudwatch_event_target" "summary_nightly" {
  rule      = aws_cloudwatch_event_rule.summary_nightly.name
  target_id = "summary-generator-nightly"
  arn       = module.summary_generator.lambda_function_arn

  input = jsonencode({ mode = "nightly" })
}

resource "aws_lambda_permission" "summary_nightly" {
  statement_id  = "AllowEventBridgeSummaryNightly"
  action        = "lambda:InvokeFunction"
  function_name = module.summary_generator.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.summary_nightly.arn
}

# 重算 sweep：把等待窗口內仍為 partial 的摘要補成 complete。
# 間隔要短於 SUMMARY_WAIT_MINUTES，否則窗口內可能一次都沒重算到。
resource "aws_cloudwatch_event_rule" "summary_backfill" {
  name                = "${var.project_name}-summary-backfill"
  description         = "重算等待窗口內仍為 partial 的摘要"
  schedule_expression = "rate(${var.summary_backfill_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "summary_backfill" {
  rule      = aws_cloudwatch_event_rule.summary_backfill.name
  target_id = "summary-generator-backfill"
  arn       = module.summary_generator.lambda_function_arn

  input = jsonencode({ mode = "backfill" })
}

resource "aws_lambda_permission" "summary_backfill" {
  statement_id  = "AllowEventBridgeSummaryBackfill"
  action        = "lambda:InvokeFunction"
  function_name = module.summary_generator.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.summary_backfill.arn
}

# =============================================================================
# 5. Session closer sweep（EventBridge 週期性收斂 — Session 關閉唯一來源）
# =============================================================================
# Session 關閉
# POST /chat/sessions/{session_id}/close
# 此 sweep 執行：idle close、BATCH#PENDING 補投、BATCH#PROCESSING lease 過期重投。

resource "aws_cloudwatch_event_rule" "session_sweep" {
  name                = "${var.project_name}-session-sweep"
  description         = "週期性收斂閒置 session 與 batch recovery（idle close / pending requeue / lease expiry）"
  schedule_expression = "rate(${var.session_sweep_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "session_sweep" {
  rule      = aws_cloudwatch_event_rule.session_sweep.name
  target_id = "session-closer-sweep"
  arn       = module.session_closer.lambda_function_arn

  input = jsonencode({
    source = "aws.events"
    sweep  = true
  })
}

resource "aws_lambda_permission" "session_sweep" {
  statement_id  = "AllowEventBridgeSessionSweep"
  action        = "lambda:InvokeFunction"
  function_name = module.session_closer.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.session_sweep.arn
}



