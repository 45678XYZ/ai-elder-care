# TTS 遠端推論端點。Lambda 維持 remote-only，不打包或載入模型。

locals {
  tts_hakka_dialects = local.asr_formo_dialects
  tts_voxhakka_dialects = toset([
    "htia_sixian",
    "htia_hailu",
    "htia_dapu",
    "htia_raoping",
    "htia_zhaoan",
  ])

  tts_remote_models = merge(
    var.tts_enable_omnivoice_endpoint ? {
      omnivoice = {
        endpoint_name = "${var.project_name}-tts-omnivoice"
        image_uri     = var.tts_omnivoice_image_uri
        model_data    = var.tts_omnivoice_model_data_url
        model_id      = "formospeech/omnivoice-hakka-community-1"
        revision      = "main"
        languages     = "hak"
        dialects      = join(",", local.tts_hakka_dialects)
        speaker       = ""
        instance_type = "ml.g4dn.xlarge"
      }
    } : {},
    var.tts_enable_voxhakka_endpoint ? {
      voxhakka = {
        endpoint_name = "${var.project_name}-tts-voxhakka"
        image_uri     = var.tts_voxhakka_image_uri
        model_data    = var.tts_voxhakka_model_data_url
        model_id      = "formospeech/yourtts-htia-240704"
        revision      = "main"
        languages     = "hak"
        dialects      = join(",", local.tts_voxhakka_dialects)
        speaker       = "XF"
        instance_type = "ml.g4dn.xlarge"
      }
    } : {},
    var.tts_enable_breezyvoice_endpoint ? {
      breezyvoice = {
        endpoint_name = "${var.project_name}-tts-breezyvoice"
        image_uri     = var.tts_breezyvoice_image_uri
        model_data    = var.tts_breezyvoice_model_data_url
        model_id      = "MediaTek-Research/BreezyVoice"
        revision      = "main"
        languages     = "zh-TW"
        dialects      = ""
        speaker       = ""
        # A10G 而非 T4：實測 g4dn.4xlarge 上每段文字要 20-25 秒，一則多段回覆動輒破分鐘。
        # 換 g5.4xlarge 約快 2-3 倍。合成仍然遠超過同步請求能等的長度，因此是非同步路徑
        # 的補強而不是替代（見 tts_worker.tf）。
        instance_type = "ml.g5.4xlarge"
      }
    } : {}
  )

  # 競賽帳號的 SageMaker real-time endpoint instance 配額（2026-08-02 查核）。
  #
  # 這些額度是帳號層級、與 ASR endpoint 共用的：ASR 目前佔用 ml.g5.2xlarge×2、
  # ml.g5.xlarge×2、ml.g4dn.2xlarge×2，因此那三種機型不列在這裡，避免 TTS 佔走之後
  # ASR 的六腔端點建不起來。
  tts_endpoint_instance_quotas = {
    "ml.g4dn.xlarge"  = 2
    "ml.g4dn.4xlarge" = 1
    "ml.g5.4xlarge"   = 1
  }
  tts_requested_instance_types = [
    for model in values(local.tts_remote_models) : model.instance_type
  ]
}

check "tts_endpoints_require_artifacts" {
  assert {
    condition = length(local.tts_remote_models) == 0 || (
      var.tts_model_artifact_bucket != "" &&
      alltrue([
        for model in values(local.tts_remote_models) :
        model.image_uri != "" && model.model_data != ""
      ])
    )
    error_message = "啟用 TTS endpoint 時必須提供 artifact bucket、image URI 與 model data URL。"
  }
}

check "tts_endpoint_instance_quotas" {
  assert {
    condition = alltrue([
      for instance_type in local.tts_requested_instance_types :
      contains(keys(local.tts_endpoint_instance_quotas), instance_type)
      ]) && alltrue([
      for instance_type, quota in local.tts_endpoint_instance_quotas :
      length([
        for requested_type in local.tts_requested_instance_types : requested_type
        if requested_type == instance_type
      ]) <= quota
    ])
    error_message = "TTS endpoint instance 配置超過競賽帳號配額，或使用未核准的機型。"
  }
}

resource "aws_iam_role" "tts_inference" {
  count = length(local.tts_remote_models) > 0 ? 1 : 0
  name  = "${var.project_name}-tts-inference"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "tts_inference_runtime" {
  count = length(local.tts_remote_models) > 0 ? 1 : 0
  name  = "tts-inference-runtime"
  role  = aws_iam_role.tts_inference[0].id

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
        Resource = "*"
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

resource "aws_iam_role_policy" "tts_inference_artifacts" {
  count = length(local.tts_remote_models) > 0 ? 1 : 0
  name  = "read-tts-model-artifacts"
  role  = aws_iam_role.tts_inference[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::${var.tts_model_artifact_bucket}",
        "arn:aws:s3:::${var.tts_model_artifact_bucket}/*",
      ]
    }]
  })
}

resource "aws_sagemaker_model" "tts" {
  for_each = local.tts_remote_models

  name               = each.value.endpoint_name
  execution_role_arn = aws_iam_role.tts_inference[0].arn

  primary_container {
    image          = each.value.image_uri
    model_data_url = each.value.model_data

    # 空值的鍵一律不送。SageMaker 不保存空字串的環境變數，讀回來就少了那個鍵，於是
    # 每次 plan 都出現差異、每次 apply 都重建 model——OmniVoice 的 TTS_DEFAULT_SPEAKER
    # 與 BreezyVoice 的 TTS_DIALECTS 都會踩到。容器讀的是 `.get(key, "")`，
    # 「鍵不存在」與「空字串」本來就等價，不送不會改變行為。
    environment = {
      for key, value in {
        TTS_MODEL_ID        = each.value.model_id
        TTS_MODEL_REVISION  = each.value.revision
        TTS_LANGUAGES       = each.value.languages
        TTS_DIALECTS        = each.value.dialects
        TTS_DEFAULT_SPEAKER = each.value.speaker
      } : key => value if value != ""
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "tts" {
  for_each = local.tts_remote_models
  name     = each.value.endpoint_name

  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.tts[each.key].name
    initial_instance_count = 1
    instance_type          = each.value.instance_type
  }
}

resource "aws_sagemaker_endpoint" "tts" {
  for_each             = local.tts_remote_models
  name                 = each.value.endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.tts[each.key].name
}

resource "aws_iam_policy" "invoke_tts_endpoints" {
  count       = length(local.tts_remote_models) > 0 ? 1 : 0
  name        = "${var.project_name}-invoke-tts-endpoints"
  description = "允許 Chat Lambda 呼叫已建立的 TTS endpoint"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sagemaker:InvokeEndpoint"
      Resource = [for endpoint in aws_sagemaker_endpoint.tts : endpoint.arn]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "chat_tts_invoke" {
  count      = length(local.tts_remote_models) > 0 ? 1 : 0
  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = aws_iam_policy.invoke_tts_endpoints[0].arn
}
