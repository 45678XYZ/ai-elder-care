# ASR 實體模型推論端點（SageMaker real-time）— remote-only 架構
#
# 七個端點構成後端 ASR 備援與客語主路徑：CE 是中文與客語共同備援；Formo
# 六腔各有一個固定 prompt 的主力端點。Lambda 只選 endpoint，絕不在 request
# 傳 prompt ID。中文主路徑是 Amazon Transcribe，不需要 SageMaker endpoint。
#
# 對應的程式側規則見 backend/src/shared/asr/README.md：
#   - router.py 只在可重試的 provider 錯誤時依序改用下一棒
#   - 每個端點固定一台 instance，不建立 autoscaling
#
# Container contract 見 docs/asr/sagemaker-inference-contract.md。
#
# 本檔預設不建立任何資源（var.asr_enable_endpoints = false）。這是刻意的：
# 模型還沒通過 staging/runtime 驗證，程式層的 model production gate 也還關著，
# 基礎設施層沒有理由先產生 GPU 費用。
#
# Fail-closed validation：啟用時必須同時提供兩個模型的 image URI、
# model-data URL 與 artifact bucket，否則 terraform validate 失敗。

locals {
  asr_endpoints_enabled = var.asr_enable_endpoints ? 1 : 0

  # CE 是獨立 gate：eval 結論不採用它，但保留可單獨開啟的能力做比較驗證。
  asr_ce_enabled = var.asr_enable_endpoints && var.asr_enable_ce_endpoint

  asr_ce_endpoint_name = "${var.project_name}-asr-ce"
  asr_ce_instance_type = "ml.g5.4xlarge"
  asr_formo_dialects = toset([
    "htia_sixian",
    "htia_hailu",
    "htia_dapu",
    "htia_raoping",
    "htia_zhaoan",
    "htia_nansixian",
  ])
  asr_formo_endpoint_names = {
    for dialect in local.asr_formo_dialects :
    dialect => "${var.project_name}-asr-formo-${replace(dialect, "htia_", "")}"
  }
  asr_formo_instance_types = {
    htia_sixian    = "ml.g5.2xlarge"
    htia_hailu     = "ml.g5.2xlarge"
    htia_dapu      = "ml.g5.xlarge"
    htia_raoping   = "ml.g5.xlarge"
    htia_zhaoan    = "ml.g4dn.2xlarge"
    htia_nansixian = "ml.g4dn.2xlarge"
  }

  # 2026-07-22 競賽帳號的 SageMaker real-time endpoint instance 配額。
  asr_endpoint_instance_quotas = {
    "ml.g5.2xlarge"   = 2
    "ml.g5.xlarge"    = 2
    "ml.g4dn.2xlarge" = 2
    "ml.g5.4xlarge"   = 1
  }
  asr_requested_instance_types = var.asr_enable_endpoints ? concat(
    local.asr_ce_enabled ? [local.asr_ce_instance_type] : [],
    values(local.asr_formo_instance_types),
  ) : []
}

# ─────────────────────────────────────────────────────────────────
# 0. Fail-closed validation — 啟用時缺少任一必要參數即失敗
# ─────────────────────────────────────────────────────────────────
check "asr_endpoints_require_all_parameters" {
  assert {
    condition = !var.asr_enable_endpoints || (
      (!local.asr_ce_enabled || (
        var.asr_ce_image_uri != "" && var.asr_ce_model_data_url != ""
      )) &&
      var.asr_formo_image_uri != "" &&
      var.asr_formo_model_data_url != "" &&
      var.asr_model_artifact_bucket != ""
    )
    error_message = "啟用 ASR 端點時，必須提供 Formo image、model data 與 artifact bucket；另外啟用 CE 時還要提供 CE 的 image 與 model data。"
  }
}

check "asr_endpoint_instance_quotas" {
  assert {
    condition = alltrue([
      for instance_type in local.asr_requested_instance_types :
      contains(keys(local.asr_endpoint_instance_quotas), instance_type)
      ]) && alltrue([
      for instance_type, quota in local.asr_endpoint_instance_quotas :
      length([
        for requested_type in local.asr_requested_instance_types : requested_type
        if requested_type == instance_type
      ]) <= quota
    ])
    error_message = "ASR endpoint instance 配置超過競賽帳號配額，或使用未核准的機型。"
  }
}

# ─────────────────────────────────────────────────────────────────
# 1. SageMaker 執行角色
# ─────────────────────────────────────────────────────────────────
resource "aws_iam_role" "asr_inference" {
  count = local.asr_endpoints_enabled

  name = "${var.project_name}-asr-inference"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# 刻意不掛 AmazonSageMakerFullAccess：推論容器只需要拉 image 與寫 log，
# 端點管理權限屬於部署者而非執行角色。
resource "aws_iam_role_policy" "asr_inference_runtime" {
  count = local.asr_endpoints_enabled

  name = "asr-inference-runtime"
  role = aws_iam_role.asr_inference[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = "*" # ECR 認證 token 本身不支援資源層級限定
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "cloudwatch:PutMetricData",
        ]
        Resource = "*"
      },
    ]
  })
}

# 模型 artifact 只讀單一 bucket，不開放整個帳號的 S3。
resource "aws_iam_role_policy" "asr_inference_artifacts" {
  count = var.asr_enable_endpoints && var.asr_model_artifact_bucket != "" ? 1 : 0

  name = "read-asr-model-artifacts"
  role = aws_iam_role.asr_inference[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::${var.asr_model_artifact_bucket}",
        "arn:aws:s3:::${var.asr_model_artifact_bucket}/*",
      ]
    }]
  })
}

# ─────────────────────────────────────────────────────────────────
# 2. 共同備援：Taiwan-Tongues-ASR-CE
# ─────────────────────────────────────────────────────────────────
resource "aws_sagemaker_model" "asr_ce" {
  count = local.asr_ce_enabled ? 1 : 0

  name               = "${var.project_name}-asr-ce"
  execution_role_arn = aws_iam_role.asr_inference[0].arn

  primary_container {
    image          = var.asr_ce_image_uri
    model_data_url = var.asr_ce_model_data_url

    environment = {
      # 模型識別與語言碼記在容器環境，讓端點自我描述、便於排查。
      ASR_MODEL_ID       = "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0"
      ASR_MODEL_REVISION = "v2.0"
      ASR_LANGUAGES      = "zh-TW,hak"
    }
  }

  lifecycle {
    precondition {
      condition     = var.asr_ce_image_uri != "" && var.asr_ce_model_data_url != ""
      error_message = "啟用 ASR 端點時必須提供 asr_ce_image_uri 與 asr_ce_model_data_url。"
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "asr_ce" {
  count = local.asr_ce_enabled ? 1 : 0

  name = "${var.project_name}-asr-ce"

  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.asr_ce[0].name
    initial_instance_count = 1
    instance_type          = local.asr_ce_instance_type
  }
}

resource "aws_sagemaker_endpoint" "asr_ce" {
  count = local.asr_ce_enabled ? 1 : 0

  name                 = local.asr_ce_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.asr_ce[0].name
}

# ─────────────────────────────────────────────────────────────────
# 3. 客語主力：FormoSpeech Whisper-v3（僅客語）
#
# 授權為 CC BY-NC 4.0（限非商業）。本專案若轉為商業服務，這個端點就不得啟用；
# 對應的程式側閘門是 ModelProductionGate.license_cleared。
# ─────────────────────────────────────────────────────────────────
resource "aws_sagemaker_model" "asr_formo" {
  for_each = var.asr_enable_endpoints ? local.asr_formo_endpoint_names : {}

  name               = each.value
  execution_role_arn = aws_iam_role.asr_inference[0].arn

  primary_container {
    image          = var.asr_formo_image_uri
    model_data_url = var.asr_formo_model_data_url

    environment = {
      ASR_MODEL_ID       = "formospeech/whisper-large-v3-taiwanese-hakka"
      ASR_MODEL_REVISION = "main"
      ASR_LANGUAGES      = "hak"
      # Whisper generation language 固定用 Chinese，輸出客語漢字。
      FORMO_GENERATION_LANGUAGE = "Chinese"
      # 每個模型資源固定一個 prompt；Lambda request contract 不含此欄位。
      FORMO_PROMPT_ID = each.key
    }
  }

  lifecycle {
    precondition {
      condition     = var.asr_formo_image_uri != "" && var.asr_formo_model_data_url != ""
      error_message = "啟用 ASR 端點時必須提供 asr_formo_image_uri 與 asr_formo_model_data_url。"
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "asr_formo" {
  for_each = var.asr_enable_endpoints ? local.asr_formo_endpoint_names : {}

  name = each.value

  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.asr_formo[each.key].name
    initial_instance_count = 1
    instance_type          = local.asr_formo_instance_types[each.key]
  }
}

resource "aws_sagemaker_endpoint" "asr_formo" {
  for_each = var.asr_enable_endpoints ? local.asr_formo_endpoint_names : {}

  name                 = each.value
  endpoint_config_name = aws_sagemaker_endpoint_configuration.asr_formo[each.key].name
}

# ─────────────────────────────────────────────────────────────────
# 4. 呼叫端授權
#
# 供 chat Lambda 呼叫七個端點。權限限定在這些 endpoint ARN，不開放
# sagemaker:InvokeEndpoint 到 "*"。
#
# chat Lambda role 需 attach 這個 policy 並注入 ASR_CONFIG_JSON 環境變數
# （見 Task 8 的 IAM 與設定注入）。
# ─────────────────────────────────────────────────────────────────
resource "aws_iam_policy" "invoke_asr_endpoints" {
  count = local.asr_endpoints_enabled

  name        = "${var.project_name}-invoke-asr-endpoints"
  description = "允許後端呼叫 ASR 主力與備援推論端點"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sagemaker:InvokeEndpoint"
      Resource = concat(
        aws_sagemaker_endpoint.asr_ce[*].arn,
        [for endpoint in aws_sagemaker_endpoint.asr_formo : endpoint.arn]
      )
    }]
  })
}

resource "aws_iam_role_policy_attachment" "chat_asr_invoke" {
  count = local.asr_endpoints_enabled

  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = aws_iam_policy.invoke_asr_endpoints[0].arn
}
