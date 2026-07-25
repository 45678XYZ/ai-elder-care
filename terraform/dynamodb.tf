# DynamoDB 五張表（依據 docs/framework.md 規格定義）：
#   elders / conversations / events / daily_summaries / routines

# 1. elders 表
resource "aws_dynamodb_table" "elders" {
  name         = "${var.project_name}-elders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "elder_id"

  attribute {
    name = "elder_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 2. conversations 表 (Base table + 3 GSIs)
resource "aws_dynamodb_table" "conversations" {
  name         = "${var.project_name}-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "elder_id"
  range_key    = "record_id"

  attribute {
    name = "elder_id"
    type = "S"
  }

  attribute {
    name = "record_id"
    type = "S"
  }

  attribute {
    name = "conversation_time_key"
    type = "S"
  }

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "session_state_key"
    type = "S"
  }

  attribute {
    name = "session_state_time_key"
    type = "S"
  }

  # GSI 1: conversations-by-time (僅 turn，按時間查詢)
  global_secondary_index {
    name            = "conversations-by-time"
    hash_key        = "elder_id"
    range_key       = "conversation_time_key"
    projection_type = "ALL"
  }

  # GSI 2: conversations-by-session (僅 turn，取得 session 有序對話)
  global_secondary_index {
    name            = "conversations-by-session"
    hash_key        = "session_id"
    range_key       = "conversation_time_key"
    projection_type = "ALL"
  }

  # Sparse GSI 3: sessions-by-state (找 idle close 與 batch recovery 候選)
  global_secondary_index {
    name            = "sessions-by-state"
    hash_key        = "session_state_key"
    range_key       = "session_state_time_key"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 3. events 表 (Base table + GSI)
resource "aws_dynamodb_table" "events" {
  name         = "${var.project_name}-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "elder_id"
  range_key    = "event_id"

  attribute {
    name = "elder_id"
    type = "S"
  }

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "event_time_key"
    type = "S"
  }

  # GSI 1: events-by-time (時間軸查詢)
  global_secondary_index {
    name            = "events-by-time"
    hash_key        = "elder_id"
    range_key       = "event_time_key"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 4. daily_summaries 表
resource "aws_dynamodb_table" "daily_summaries" {
  name         = "${var.project_name}-daily_summaries"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "elder_id"
  range_key    = "date"

  attribute {
    name = "elder_id"
    type = "S"
  }

  attribute {
    name = "date"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}

# 5. routines 表 (Base table + 2 GSIs)
resource "aws_dynamodb_table" "routines" {
  name         = "${var.project_name}-routines"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "routine_id"
  range_key    = "version"

  attribute {
    name = "routine_id"
    type = "S"
  }

  attribute {
    name = "version"
    type = "N"
  }

  attribute {
    name = "elder_id"
    type = "S"
  }

  attribute {
    name = "current_sort_key"
    type = "S"
  }

  attribute {
    name = "version_time_key"
    type = "S"
  }

  # Sparse GSI 1: routines-current-by-elder (僅 is_current=true 存在)
  global_secondary_index {
    name            = "routines-current-by-elder"
    hash_key        = "elder_id"
    range_key       = "current_sort_key"
    projection_type = "ALL"
  }

  # GSI 2: routine-versions-by-elder (依長者查歷史有效版本)
  global_secondary_index {
    name            = "routine-versions-by-elder"
    hash_key        = "elder_id"
    range_key       = "version_time_key"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project     = var.project_name
    Environment = "production"
  }
}
