# EventBridge
#   - 每晚觸發 summary_generator（台灣時間）：TODO
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
  arn       = module.session_closer.lambda_function_arn

  # handler 依 source 欄位分派；帶 sweep 旗標讓本地測試也能觸發同一條路徑
  input = jsonencode({ sweep = true })
}

resource "aws_lambda_permission" "session_sweep" {
  statement_id  = "AllowEventBridgeSweep"
  action        = "lambda:InvokeFunction"
  function_name = module.session_closer.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.session_sweep.arn
}
