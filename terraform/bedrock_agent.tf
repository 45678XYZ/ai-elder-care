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
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-sonnet-5",
          "arn:aws:bedrock:${var.aws_region}:*:inference-profile/us.anthropic.claude-sonnet-5",
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

# 3. 部署 Bedrock Agent 本體 (使用 Claude 5 Sonnet)
resource "aws_bedrockagent_agent" "elder_companion_agent" {
  agent_name                  = "${var.project_name}-companion"
  
  # 修正：移除多餘的 3，並建議加上 us. 啟用跨區路由，確保照護系統的高可用性
  foundation_model            = "us.anthropic.claude-sonnet-5" 
  
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
    lambda = aws_lambda_function.tools.arn
  }

  # 定義 Inline Functions (無需外部 Swagger/OpenAPI JSON 檔案)
  function_schema {
    # 工具一：查詢今日排程
    member_functions {
      functions {
        name        = "get_today_routines"
        description = "Retrieve a list of scheduled routines and their completion status for a specific elder on a given date."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "date"
            type        = "string"
            description = "查詢的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
            required    = true
          }
      }

      # 工具二：完成行程
      functions {
        name        = "complete_routine"
        description = "Mark a specific routine as completed and log a life event for the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "routine_id"
            type        = "string"
            description = "要完成的行程 ID，例如 rtn_001"
            required    = true
          }
          parameters {
            map_block_key = "date"
            type        = "string"
            description = "完成的日期，格式為 YYYY-MM-DD，例如 2026-07-20"
            required    = true
          }
          parameters {
            map_block_key = "completed_by"
            type        = "string"
            description = "完成行程的角色，口語回報一律填 conversation"
            required    = true
          }
      }

      # 工具三：建立新行程
      functions {
        name        = "create_routine"
        description = "Create a new scheduled routine (either one-time or recurring) for the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "title"
            type        = "string"
            description = "行程的標題或內容，例如：吃血壓藥、看心臟科"
            required    = true
          }
          parameters {
            map_block_key = "type"
            type        = "string"
            description = "行程類型分類，例如：medication, diet, activity, wellbeing, other"
            required    = true
          }
          parameters {
            map_block_key = "time"
            type        = "string"
            description = "行程時間，格式為 HH:MM，例如 15:30"
            required    = true
          }
          parameters {
            map_block_key = "freq"
            type        = "string"
            description = "頻率：daily, weekly, once"
            required    = true
          }
          parameters {
            map_block_key = "date"
            type        = "string"
            description = "如果是單次(once)行程，必須提供日期 YYYY-MM-DD；每日或每週則免"
            required    = false
          }
      }

      # 工具三.一：更新行程
      functions {
        name        = "update_routine"
        description = "Update an existing scheduled routine (e.g., change time, title, or frequency) for the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "routine_id"
            type        = "string"
            description = "要修改的行程 ID，例如 rtn_001"
            required    = true
          }
          parameters {
            map_block_key = "title"
            type        = "string"
            description = "行程的標題或內容"
            required    = false
          }
          parameters {
            map_block_key = "type"
            type        = "string"
            description = "行程類型分類：medication, diet, activity, wellbeing, other"
            required    = false
          }
          parameters {
            map_block_key = "time"
            type        = "string"
            description = "行程時間，格式為 HH:MM"
            required    = false
          }
          parameters {
            map_block_key = "freq"
            type        = "string"
            description = "頻率：daily, weekly, once"
            required    = false
          }
          parameters {
            map_block_key = "date"
            type        = "string"
            description = "單次行程的日期 YYYY-MM-DD"
            required    = false
          }
          parameters {
            map_block_key = "remind"
            type        = "boolean"
            description = "是否發送提醒"
            required    = false
          }
          parameters {
            map_block_key = "active"
            type        = "boolean"
            description = "是否啟用"
            required    = false
          }
      }

      # 工具三.二：停用/刪除行程
      functions {
        name        = "deactivate_routine"
        description = "Deactivate or cancel an existing scheduled routine for the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "routine_id"
            type        = "string"
            description = "要停用的行程 ID，例如 rtn_001"
            required    = true
          }
      }

      # 工具四：查詢近期生活事件歷史
      functions {
        name        = "get_recent_events"
        description = "Retrieve recent life events, activities, and recorded health signals for the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "event_type"
            type        = "string"
            description = "可選的事件類型過濾，例如：routine_completion, wellbeing, activity, family, diet, other"
            required    = false
          }
      }

      # 工具五：查詢長者個人喜好與家屬檔案
      functions {
        name        = "get_elder_profile"
        description = "Retrieve personal preferences, hobbies, health notes, and family members of the elder."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
      }

      # 工具六：主動提醒待辦行程
      functions {
        name        = "remind_pending_routines"
        description = "Check and retrieve pending scheduled routines for the elder to generate warm reminders."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "date"
            type        = "string"
            description = "可選的查詢日期，格式為 YYYY-MM-DD，預設為今天"
            required    = false
          }
      }

      # 工具七：發送照護者即時緊急警報與摘要通知（醫療級安全機制）
      functions {
        name        = "notify_caregiver"
        description = <<-DESC
          Send SNS notification to caregiver. Use category to control safety behavior:
          - emergency: First-time urgent alert (fall/chest pain/cannot move). Has 5-min cooldown. Writes DB event.
          - critical_escalation: Condition worsening (new bleeding/fainting/severe pain). BYPASSES cooldown. Use when elder reports new severe symptoms after initial emergency.
          - mitigation: Elder verbally says they feel better. Sets status to WARNING (pending caregiver confirmation). Does NOT resolve the alert. Requires active emergency to exist.
          - routine: Scheduled task completion digest.
          - summary: Daily health summary report.
          IMPORTANT: Only caregivers (not elders) can fully resolve an alert via the App.
        DESC
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "category"
            type        = "string"
            description = "通知類別：emergency | critical_escalation | mitigation | routine | summary"
            required    = true
          }
          parameters {
            map_block_key = "message"
            type        = "string"
            description = "要推播給照護者的詳細訊息內容（請包含事件的人事時地）"
            required    = true
          }
          parameters {
            map_block_key = "context_event_id"
            type        = "string"
            description = "選填。用於 mitigation 或 critical_escalation 時，傳入對應的原始緊急事件 event_id（由系統在 emergency 觸發時回傳），確保 Context Matching 精準更新同一事件，防止誤蓋其他事件記錄。"
            required    = false
          }
          parameters {
            map_block_key = "rag_content"
            type        = "string"
            description = "選填。來自 RAG 衛教知識庫的相關急救或照護指南內容。將折疊附加至 Email 附錄（附免責聲明），不影響信件主要人事時地資訊版面。"
            required    = false
          }
      }

      # 工具八：查詢長者近幾日每日健康摘要（提供縱向健康趨勢）
      functions {
        name        = "get_daily_summaries"
        description = "Retrieve recent daily health summaries for the elder to understand health trends over multiple days. Use this when the elder or caregiver asks about recent health status, trends, or when you need context about the elder's health over the past few days."
          parameters {
            map_block_key = "elder_id"
            type        = "string"
            description = "長者的唯一識別 ID，例如 eld_001"
            required    = true
          }
          parameters {
            map_block_key = "days"
            type        = "integer"
            description = "查詢最近幾天的摘要，預設為 3 天（含今天），最多 7 天。例如 days=3 代表今天+昨天+前天。"
            required    = false
          }
        }

      # 工具九：回顧本次對話歷史（補救 Bedrock Session 20 分鐘過期問題）
      functions {
        name        = "get_recent_conversations"
        description = "Retrieve the most recent conversation turns with the elder. Use this tool when you feel you have lost context of the current conversation, for example after a session timeout, to recall what was discussed earlier in this session."
        parameters {
          map_block_key = "elder_id"
          type        = "string"
          description = "長者的唯一識別 ID，例如 eld_001"
          required    = true
        }
        parameters {
          map_block_key = "limit"
          type        = "integer"
          description = "要回顧最近幾句對話，預設為 8，最多 15。建議用預設值即可。"
          required    = false
        }
    }
      }
    }
  }

# 5. 授權 Bedrock 調用 Tools Lambda
resource "aws_lambda_permission" "allow_bedrock_to_invoke_tools" {
  statement_id  = "AllowBedrockInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tools.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = aws_bedrockagent_agent.elder_companion_agent.agent_arn
}


# 6. 將 Bedrock Knowledge Base (衛教知識庫) 綁定至 Agent
# KB 本體見 bedrock_kb.tf 的 aws_bedrockagent_knowledge_base.kb
#
# description 不是註解，是**給模型看的**：agent 靠它判斷「這個問題該不該查 KB」。
# 所以要對得上 data/knowledge/ 裡實際有的 29 篇主題——沒被提到的領域，agent 可能
# 根本不會想到要檢索（長輩問「有沒有人可以幫忙照顧」，描述裡看不出涵蓋喘息服務，
# 就會直接用自己的知識回答而繞過 KB）。增刪文件時這段要跟著更新。
#
# 上限 200 **bytes** 而非字元，中文一字 3 bytes，實際只放得下約 66 字。取捨：
# 病名寫具體的（使用者問「血壓高怎麼辦」，「慢性病照護」對不上），氣喘、肺阻塞、
# 骨鬆、代謝症候群因額度不足捨去；服務用長輩會講的詞，不用官方全名。
# 「什麼情況該檢索」的引導句塞不進來——若實測發現該查而沒查，補在本檔上方
# aws_bedrockagent_agent 的 instruction，那裡沒有這個限制。
resource "aws_bedrockagent_agent_knowledge_base_association" "health_kb" {
  agent_id             = aws_bedrockagent_agent.elder_companion_agent.id
  agent_version        = "DRAFT"
  description          = "長者衛教與長照知識庫：慢性病（高血壓、糖尿病、中風）、失智、跌倒預防、輔具、口腔照護、喘息服務、交通接送、照顧服務、季節保健"
  knowledge_base_id    = aws_bedrockagent_knowledge_base.kb.id
  knowledge_base_state = "ENABLED"
}
