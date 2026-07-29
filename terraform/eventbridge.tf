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

resource "aws_lambda_function" "daily_digest" {
  function_name = "${var.project_name}-daily-digest"
  role          = aws_iam_role.daily_digest_role.arn
  handler       = "handlers.daily_digest.handler"
  runtime       = "python3.11"
  timeout       = 120  # 晚報需掃描所有長者，給足夠時間
  memory_size   = 256

  filename = "${path.module}/build/backend.zip"

  environment {
    variables = {
      TABLE_ELDERS          = aws_dynamodb_table.elders.name
      TABLE_ROUTINES        = aws_dynamodb_table.routines.name
      TABLE_EVENTS          = aws_dynamodb_table.events.name
      TABLE_DAILY_SUMMARIES = aws_dynamodb_table.daily_summaries.name
      CAREGIVER_NOTIFY_TOPIC_ARN = aws_sns_topic.caregiver_notifications.arn
      AWS_REGION_NAME       = var.aws_region
    }
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
      Resource = aws_lambda_function.daily_digest.arn
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
    mode = "OFF"  # 嚴格固定時間，不允許彈性窗口
  }

  # cron(分 時 日 月 星期 年) — UTC 14:00 = 台灣時間 22:00
  schedule_expression          = "cron(0 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.daily_digest.arn
    role_arn = aws_iam_role.eventbridge_scheduler_role.arn

    # 傳給 Lambda 的事件 payload（空 JSON 即可，Lambda 自行查詢所有長者）
    input = jsonencode({
      source    = "aws.scheduler"
      task_type = "daily_digest"
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
  function_name = aws_lambda_function.daily_digest.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.daily_digest_schedule.arn
}
