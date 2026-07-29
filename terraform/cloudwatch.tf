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

# 分塊器持續走機械切分，代表 LLM 分塊或 embedding 一直失敗——事件品質會默默下降，
# 不會有任何錯誤浮上來，所以要靠指標抓。
resource "aws_cloudwatch_metric_alarm" "chunker_fallback" {
  alarm_name          = "${var.project_name}-chunker-fallback"
  alarm_description   = "分塊器頻繁退回機械切分，事件品質可能已下降"
  namespace           = var.metrics_namespace
  metric_name         = "ChunkerFallback"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

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
