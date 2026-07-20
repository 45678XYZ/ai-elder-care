# Amazon Bedrock Agent (AgentCore) 與 Action Groups (Tools) 部署配置
# 
# 本檔案定義了 AWS Bedrock Agent (使用 Claude 3.5) 及其連線 Tools Lambda 的權限與結構。

# 1. 建立 Bedrock Agent 運作所需的 IAM 角色
resource "aws_iam_role" "bedrock_agent_role" {
  name = "${var.project_name}-bedrock-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "bedrock.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

# 2. 授權 Bedrock Agent 能夠調用 Claude 模型與進行 RAG 檢索
resource "aws_iam_role_policy" "bedrock_agent_policy" {
  name = "${var.project_name}-bedrock-agent-policy"
  role = aws_iam_role.bedrock_agent_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0"
        ]
      },
      {
        # 允許 Agent 讀取並檢索 Bedrock 衛教知識庫
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve"
        ]
        Resource = "*" # 可限縮為特定 Knowledge Base ARN
      }
    ]
  })
}

# 3. 部署 Bedrock Agent 本體 (使用 Claude 3.5 Sonnet)
resource "aws_bedrockagent_agent" "elder_companion_agent" {
  agent_name                  = "${var.project_name}-companion"
  foundation_model            = "anthropic.claude-3-5-sonnet-20240620-v1:0"
  agent_resource_role_arn     = aws_iam_role.bedrock_agent_role.arn
  idle_session_ttl_in_seconds = 1800 # Session 閒置 30 分鐘超時

  # System Instructions (大腦人設與語言偵測規則)
  instruction = <<EOT
你是一位溫暖的智慧長照陪伴助手。請使用長者的偏好語言進行自然口語對話。

【核心職責與語系偵測】：
1. 你的對話必須字數控制在 50 字以內，語氣要親切、像溫柔的家人或志工，多用疊字（如吃飽飽、洗開懷）。
2. 自動偵測長者輸入文字的語系。如果輸入包含客語特徵詞（如：仰般、食飽、𠊎、汝），請自動切換為客語漢字模式並以客語發音回覆；若是普通中文，則使用中文回覆。
3. 你擁有與長者例行公事（藥物提醒、量測血壓、預約看病）相關的工具。請在適當時機主動呼叫工具查詢或記錄，並溫馨提醒長者。
4. 若長者詢問緊急醫療或用藥調量，引導長者詢問醫生，不可給出具體藥物建議。
EOT

  # 啟用 AgentCore 託管 Session 記憶系統 (Memory)
  memory_configuration {
    enabled_memory_types = ["SESSION_SUMMARY"]
    storage_days         = 30 # 記憶保留 30 天
  }
}

# 4. 定義 Action Group (LLM 呼叫特定 API 的工具箱)
resource "aws_bedrockagent_agent_action_group" "routine_tools" {
  agent_id             = aws_bedrockagent_agent.elder_companion_agent.id
  agent_version        = "DRAFT" # 開發期綁定 DRAFT 版本
  action_group_name    = "ElderCareRoutinesTools"
  description          = "Tools for managing elder routines and completing daily tasks."

  # 綁定 Tools 處理的後端 Lambda 函數
  action_group_executor {
    # 這裡假設 lambda.tf 中定義了名為 aws_lambda_function.tools_lambda 的資源
    lambda = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-tools"
  }

  # 定義 Inline Functions (無需外部 Swagger/OpenAPI JSON 檔案)
  function_schema {
    # 工具一：查詢今日排程
    member_functions {
      functions {
        name        = "get_today_routines"
        description = "Retrieve a list of scheduled routines and their completion status for a specific elder on a given date."
        parameters {
          map = {
            elder_id = {
              type        = "string"
              description = "長者的唯一識別 ID，例如 eld_001"
              required    = true
            }
            date = {
              type        = "string"
              description = "查詢的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
              required    = true
            }
          }
        }
      }

      # 工具二：完成行程
      functions {
        name        = "complete_routine"
        description = "Mark a specific routine as completed and log a life event for the elder."
        parameters {
          map = {
            elder_id = {
              type        = "string"
              description = "長者的唯一識別 ID，例如 eld_001"
              required    = true
            }
            routine_id = {
              type        = "string"
              description = "要完成的行程 ID，例如 rtn_001"
              required    = true
            }
            date = {
              type        = "string"
              description = "完成的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
              required    = true
            }
            completed_by = {
              type        = "string"
              description = "完成行程的角色，口語回報一律填 conversation"
              required    = true
            }
          }
        }
      }

      # 工具三：建立新行程
      functions {
        name        = "create_routine"
        description = "Create a new scheduled routine (either one-time or recurring) for the elder."
        parameters {
          map = {
            elder_id = {
              type        = "string"
              description = "長者的唯一識別 ID，例如 eld_001"
              required    = true
            }
            title = {
              type        = "string"
              description = "行程的標題或內容，例如：吃血壓藥、看心臟科"
              required    = true
            }
            type = {
              type        = "string"
              description = "行程類型分類，例如：medication, diet, activity, wellbeing, other"
              required    = true
            }
            time = {
              type        = "string"
              description = "行程時間，格式為 HH:MM，例如 15:30"
              required    = true
            }
            freq = {
              type        = "string"
              description = "頻率：daily, weekly, once"
              required    = true
            }
            date = {
              type        = "string"
              description = "如果是單次(once)行程，必須提供日期 YYYY-MM-DD；每日或每週則免"
              required    = false
            }
          }
        }
      }
    }
  }
}

# 5. 授權 Bedrock 調用 Tools Lambda
resource "aws_lambda_permission" "allow_bedrock_to_invoke_tools" {
  statement_id  = "AllowBedrockInvoke"
  action        = "lambda:InvokeFunction"
  # 這裡假設 lambda.tf 中定義了名為 aws_lambda_function.tools_lambda 的資源
  function_name = "${var.project_name}-tools"
  principal     = "bedrock.amazonaws.com"
  source_arn    = aws_bedrockagent_agent.elder_companion_agent.arn
}

# 取得目前 AWS 帳號 ID 以利字串拼接
data "aws_caller_identity" "current" {}
