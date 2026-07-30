# SQS：session close 後的 batch 派送佇列與 DLQ
#
# 流程：session closer 在 closed 之後送訊息 → batch extractor 消費 → 重試耗盡進 DLQ
#      → DLQ reconciler 以 session_id + session_snapshot_hash 條件式收斂為 failed 並告警
#
# 冪等性不依賴 SQS：closed 之後 SendMessage 可能中斷，因此 EventBridge 會週期性掃
# BATCH#PENDING 補投；consumer 端以 session 的 batch 狀態機與 lease 去重（見 backend/src/shared/sessions.py）。

resource "aws_sqs_queue" "batch_dlq" {
  name = "${var.project_name}-batch-dlq"

  # DLQ 保留久一點，人工 replay 前要有時間排查
  message_retention_seconds = 1209600 # 14 天
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "batch" {
  name = "${var.project_name}-batch"

  # visibility timeout 必須 >= Lambda timeout，否則同一訊息會在處理中被重投
  visibility_timeout_seconds = var.batch_lambda_timeout * 6
  message_retention_seconds  = 345600 # 4 天
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.batch_dlq.arn
    # 節流與暫時性故障靠這幾次重試吸收；仍失敗才進 DLQ 交由 reconciler 收斂
    maxReceiveCount = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "batch_dlq" {
  queue_url = aws_sqs_queue.batch_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.batch.arn]
  })
}
