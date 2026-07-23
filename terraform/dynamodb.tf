# DynamoDB 六張表（見框架文件「資料模型」）：
#   elders / conversations / events / daily_summaries / memories / routines
#
# TODO: 上述六表的 key schema 與 GSI（events、conversations 依 elder_id＋時間查詢）

# auth 身分對應表：Cognito sub → elder_id，供 pre-token-generation trigger 查詢後注入
# elder_id claim（見 cognito.tf、backend/src/handlers/pre_token_generation.py）。與上述
# 六張「資料」表分屬不同關注點，屬 auth 領域。
resource "aws_dynamodb_table" "elder_accounts" {
  name         = "${var.project_name}-elder-accounts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sub"

  attribute {
    name = "sub"
    type = "S"
  }
}
