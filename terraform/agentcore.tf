# Bedrock AgentCore：對話大腦 Runtime、託管長期記憶 Memory 與部署包
#
# 取代原本的 Bedrock Agents Classic（aws_bedrockagent_agent + action group）：Classic 自
# 2026-07-30 起不再開放新客戶，新帳號或新區域建不起來，整套 IaC 會直接失效。
#
# 大腦邏輯改由 backend/src/agentcore_runtime/ 的 LangGraph 圖自行實作（人設、工具選擇、
# 知識庫檢索時機都在程式裡，見該套件的 prompts.py）。工具仍留在 tools Lambda，Runtime 以
# lambda:InvokeFunction 呼叫：tools.py 的 notify_caregiver 冷卻期用的是 process 內的
# in-memory 狀態，搬進長時間常駐的 Runtime 會變成跨長者共用，語意與現在不同。
#
# 部署方式選 zip（code_configuration）而非容器：與其餘 Lambda 共用同一份 backend/src 與
# 打包流程，且不必處理「ECR 要先有 image、image 要先有 ECR」的首次 apply 死結。日後若
# runtime 需要容器才裝得下的套件，把 agent_runtime_artifact 換成 container_configuration
# 即可，其餘資源與 chat.py 都不用動。

# =============================================================================
# 1. 部署包：LangGraph runtime 的原始碼 zip
# =============================================================================
# code_configuration 的 entry_point 以 zip 根目錄為基準，因此 main.py 除了留在套件內，
# 另外複製一份到根目錄。main.py 只是一層 import 的薄殼（見該檔），重複一份沒有維護成本。

locals {
  # 只收 agentcore_runtime 這一個套件：handlers/ 由 tools Lambda 自己執行，extraction/ 只走
  # batch 路徑，兩者連同 taxonomy 資產都不必進這個部署包
  agent_runtime_source_path = [
    {
      path = "${path.module}/../backend/src/agentcore_runtime/main.py"
    },
    {
      path          = "${path.module}/../backend/src/__init__.py"
      prefix_in_zip = "src"
    },
    {
      path          = "${path.module}/../backend/src/agentcore_runtime"
      prefix_in_zip = "src/agentcore_runtime"
    },
    {
      path             = "${path.module}/../backend/agentcore_requirements.txt"
      pip_requirements = true
    },
  ]
}

# bucket 名稱全域唯一，帶 account id 避免撞名
resource "aws_s3_bucket" "agent_runtime_code" {
  bucket = "${var.project_name}-agent-runtime-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "agent_runtime_code" {
  bucket = aws_s3_bucket.agent_runtime_code.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 開版本控管：code_configuration 帶 version_id，更新部署包不必換 key 也能讓 Runtime 認得是新版
resource "aws_s3_bucket_versioning" "agent_runtime_code" {
  bucket = aws_s3_bucket.agent_runtime_code.id

  versioning_configuration {
    status = "Enabled"
  }
}

# 只打包不建 Lambda：借用社群模組既有的 pip 安裝與 zip 上傳流程，產物交給 AgentCore Runtime
module "agent_runtime_package" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  create_function = false
  create_package  = true

  store_on_s3 = true
  s3_bucket   = aws_s3_bucket.agent_runtime_code.id
  s3_prefix   = "agent-runtime/"

  source_path   = local.agent_runtime_source_path
  artifacts_dir = "${path.module}/build"

  # AgentCore Runtime 掃描 zip 內每個 .so 的 ELF header，不是 arm64 就拒收，因此依賴必須
  # 在 arm64 Linux 容器裡裝（見 lambda.tf 的 docker_build_options）。這裡只打包不建函數，
  # 所以不需要 architectures。
  build_in_docker           = true
  docker_additional_options = local.docker_build_options

  depends_on = [aws_s3_bucket_versioning.agent_runtime_code]
}

# =============================================================================
# 2. 託管長期記憶（Memory）
# =============================================================================
# 對應原本 Classic agent 的 memory_configuration。維持 docs/framework.md 的架構決策：
# 長期記憶由 AWS 託管，不自建 DynamoDB memories 表。Runtime 內以 langgraph-checkpoint-aws
# 的 AgentCoreMemorySaver 接上，因此 LangGraph 的 checkpoint 能力也一併取得。

resource "aws_bedrockagentcore_memory" "companion" {
  name        = "ai_elder_care_companion_memory"
  description = "長者對話的長期記憶；由 chat Lambda 以 runtimeSessionId 隔離不同長者"

  # 對齊原本 Classic agent 的 storage_days = 30
  event_expiry_duration = var.agent_memory_expiry_days
}

# =============================================================================
# 3. Runtime 執行角色
# =============================================================================

resource "aws_iam_role" "agentcore_runtime" {
  name = "${var.project_name}-agentcore-runtime-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        # Runtime 建立前還沒有實際 ID，用萬用字元鎖同帳號下的資源，防混淆代理
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
        }
      }
    }]
  })
}

# 最小權限：模型呼叫只給大腦與備援那兩顆
resource "aws_iam_role_policy" "agentcore_runtime_invoke_model" {
  name = "invoke-companion-model"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      # inference profile 會再路由到底層 foundation model，兩種 ARN 都要放行
      Resource = [
        "arn:aws:bedrock:${var.aws_region}::foundation-model/*",
        "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
      ]
    }]
  })
}

# 衛教知識庫檢索：原本由 Classic agent 的 knowledge base association 代勞，
# 現在改由 runtime 內的 search_health_knowledge 工具自行呼叫 Retrieve
resource "aws_iam_role_policy" "agentcore_runtime_retrieve_kb" {
  name = "retrieve-health-kb"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:Retrieve"
      Resource = aws_bedrockagent_knowledge_base.kb.arn
    }]
  })
}

# 託管記憶的資料面操作；只給這一份 memory
resource "aws_iam_role_policy" "agentcore_runtime_memory" {
  name = "rw-companion-memory"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:DeleteEvent",
        "bedrock-agentcore:ListSessions",
        "bedrock-agentcore:ListActors",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord",
      ]
      Resource = "${aws_bedrockagentcore_memory.companion.arn}*"
    }]
  })
}

# 工具呼叫：只給 tools Lambda，商業邏輯與 DynamoDB／SNS 權限仍留在該 Lambda 的角色上
resource "aws_iam_role_policy" "agentcore_runtime_invoke_tools" {
  name = "invoke-tools-lambda"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = module.tools.lambda_function_arn
    }]
  })
}

# 讀取部署包：AgentCore 以本角色從 S3 取 zip
resource "aws_iam_role_policy" "agentcore_runtime_read_code" {
  name = "read-runtime-package"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.agent_runtime_code.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetBucketLocation"
        Resource = aws_s3_bucket.agent_runtime_code.arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "agentcore_runtime_logs" {
  name = "write-logs"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
    }]
  })
}

# =============================================================================
# 4. Runtime 與 Endpoint
# =============================================================================

resource "aws_bedrockagentcore_agent_runtime" "companion" {
  # 名稱只接受 [a-zA-Z][a-zA-Z0-9_]{0,47}，不能沿用其他資源的 ${project_name}- 連字號前綴
  agent_runtime_name = "ai_elder_care_companion"
  description        = "長照陪伴對話大腦；LangGraph 狀態機 + tools Lambda + 衛教知識庫"
  role_arn           = aws_iam_role.agentcore_runtime.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_11"

      code {
        s3 {
          bucket     = module.agent_runtime_package.s3_object.bucket
          prefix     = module.agent_runtime_package.s3_object.key
          version_id = module.agent_runtime_package.s3_object.version_id
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  # 回應由 chat Lambda 一次讀完才進 TTS，沒有逐字消費者，因此用一般 HTTP JSON 不做 SSE
  protocol_configuration {
    server_protocol = "HTTP"
  }

  environment_variables = {
    TOOLS_FUNCTION_NAME = module.tools.lambda_function_name
    AGENT_MEMORY_ID     = aws_bedrockagentcore_memory.companion.id
    KNOWLEDGE_BASE_ID   = aws_bedrockagent_knowledge_base.kb.id
    AGENT_MODEL_ID      = var.agent_model_id != "" ? var.agent_model_id : var.bedrock_model_id
    KB_RETRIEVE_TOP_K   = tostring(var.agent_kb_top_k)
  }

  depends_on = [
    aws_iam_role_policy.agentcore_runtime_invoke_model,
    aws_iam_role_policy.agentcore_runtime_retrieve_kb,
    aws_iam_role_policy.agentcore_runtime_memory,
    aws_iam_role_policy.agentcore_runtime_invoke_tools,
    aws_iam_role_policy.agentcore_runtime_read_code,
  ]
}

# chat Lambda 以 qualifier 指定端點；固定名稱讓部署新版 runtime 不必改 Lambda 環境變數
resource "aws_bedrockagentcore_agent_runtime_endpoint" "live" {
  name             = "live"
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.companion.agent_runtime_id
  description      = "chat Lambda 呼叫的正式端點"
}
