# CloudWatch 告警
#
# 指標本身由 Lambda 以 EMF 寫進 stdout（見 backend/src/shared/metrics.py），
# 不需要 PutMetricData 權限。這裡只挑「需要有人處理」的少數狀況設告警：
# 告警太多等於沒有告警，所以刻意不對每個指標都設。

# DLQ 有訊息代表某個 session 的 batch 重試耗盡，已由 reconciler 收斂為 failed，
# 需要人工確認後 replay（failed → pending）。這是唯一需要人介入的正常流程終點。
resource "aws_cloudwatch_metric_alarm" "batch_dlq_not_empty" {
  alarm_name          = "${var.project_name}-batch-dlq-not-empty"
  alarm_description   = "batch DLQ 有待人工 replay 的 session"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.batch_dlq.name
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
  ok_actions    = [aws_sns_topic.batch_alerts.arn]
}

# batch extractor 的未處理例外。handler 已把 retryable 與 permanent 分開處理，
# 因此這裡的 Errors 代表「連分類都失敗」的未預期狀況。
resource "aws_cloudwatch_metric_alarm" "batch_extractor_errors" {
  alarm_name          = "${var.project_name}-batch-extractor-errors"
  alarm_description   = "batch extractor 出現未預期例外"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = module.batch_extractor.lambda_function_name
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}


# session sweep 若持續在補投，代表 close 之後的 SendMessage 常常失敗；
# 機制上能自我修復，但持續發生是要修的問題。
resource "aws_cloudwatch_metric_alarm" "pending_requeue" {
  alarm_name          = "${var.project_name}-batch-pending-requeue"
  alarm_description   = "batch 派送持續需要 sweep 補投"
  namespace           = var.metrics_namespace
  metric_name         = "SessionSweep"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    SweepKind = "pending_requeued"
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}

# 排程摘要的未處理例外。generator 已把單一長者的失敗吞下並繼續（見
# backend/src/handlers/summary_generator.py），因此這裡的 Errors 代表整批失敗，
# 那一晚所有長者都不會有摘要。
resource "aws_cloudwatch_metric_alarm" "summary_generator_errors" {
  alarm_name          = "${var.project_name}-summary-generator-errors"
  alarm_description   = "排程摘要生成整批失敗"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = module.summary_generator.lambda_function_name
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}

# 摘要長期停在 partial，代表 batch 沒收斂：照護者看到的每日紀錄會一直缺一段。
# 用 partial 維度的生成次數當訊號，因為「該完整卻不完整」不會產生任何錯誤。
resource "aws_cloudwatch_metric_alarm" "summary_partial_persistent" {
  alarm_name          = "${var.project_name}-summary-partial-persistent"
  alarm_description   = "摘要持續為 partial，batch materialization 可能卡住"
  namespace           = var.metrics_namespace
  metric_name         = "SummaryGenerated"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  threshold           = var.summary_partial_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DataStatus = "partial"
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}


# API Gateway 5XX 錯誤告警：前端收到 5XX 代表後端 Lambda 崩潰或 API GW 整合失敗
resource "aws_cloudwatch_metric_alarm" "api_gateway_5xx" {
  alarm_name          = "${var.project_name}-api-gateway-5xx"
  alarm_description   = "API Gateway 出現 5XX 伺服器端錯誤"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiName = aws_api_gateway_rest_api.api.name
    Stage   = aws_api_gateway_stage.v1.stage_name
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}

# session_closer 未處理例外告警
resource "aws_cloudwatch_metric_alarm" "session_closer_errors" {
  alarm_name          = "${var.project_name}-session-closer-errors"
  alarm_description   = "session closer 執行出現未預期例外"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = module.session_closer.lambda_function_name
  }

  alarm_actions = [aws_sns_topic.batch_alerts.arn]
}
