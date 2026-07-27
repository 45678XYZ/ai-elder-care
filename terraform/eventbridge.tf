# EventBridge
#   - 每晚觸發 summary_generator（台灣時間）
#   - 週期性重算仍為 partial 的摘要（等待窗口內）
#   - 週期性 session closer：idle close、BATCH#PENDING 補投、BATCH#PROCESSING lease 過期重投
#
# 三種 sweep 由同一支 Lambda 一次跑完（見 backend/src/handlers/session_closer.py 的 run_sweep）。
# 這條規則是可靠性的最後一道：closed 之後 SendMessage 中斷、worker 中途死亡、App 沒呼叫
# close 的閒置 session，都靠它收斂，否則那些 session 的一般事件永遠不會 materialize。

resource "aws_cloudwatch_event_rule" "session_sweep" {
  name        = "${var.project_name}-session-sweep"
  description = "週期性收斂 idle session 與補投 batch"

  # 間隔要短於 batch lease，lease 過期的工作才不會等太久才被接管
  schedule_expression = "rate(${var.session_sweep_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "session_sweep" {
  rule      = aws_cloudwatch_event_rule.session_sweep.name
  target_id = "session-closer"
  arn       = aws_lambda_function.session_closer.arn

  # handler 依 source 欄位分派；帶 sweep 旗標讓本地測試也能觸發同一條路徑
  input = jsonencode({ sweep = true })
}

resource "aws_lambda_permission" "session_sweep" {
  statement_id  = "AllowEventBridgeSweep"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.session_closer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.session_sweep.arn
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
  arn       = aws_lambda_function.summary_generator.arn

  input = jsonencode({ mode = "nightly" })
}

resource "aws_lambda_permission" "summary_nightly" {
  statement_id  = "AllowEventBridgeSummaryNightly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.summary_generator.function_name
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
  arn       = aws_lambda_function.summary_generator.arn

  input = jsonencode({ mode = "backfill" })
}

resource "aws_lambda_permission" "summary_backfill" {
  statement_id  = "AllowEventBridgeSummaryBackfill"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.summary_generator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.summary_backfill.arn
}
