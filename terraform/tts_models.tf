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
      }
    } : {}
  )
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
    environment = {
      TTS_MODEL_ID        = each.value.model_id
      TTS_MODEL_REVISION  = each.value.revision
      TTS_LANGUAGES       = each.value.languages
      TTS_DIALECTS        = each.value.dialects
      TTS_DEFAULT_SPEAKER = each.value.speaker
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "tts" {
  for_each = local.tts_remote_models
  name     = each.value.endpoint_name

  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.tts[each.key].name
    initial_instance_count = var.tts_min_instances
    instance_type          = var.tts_instance_type
  }
}

resource "aws_sagemaker_endpoint" "tts" {
  for_each             = local.tts_remote_models
  name                 = each.value.endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.tts[each.key].name
}

resource "aws_appautoscaling_target" "tts" {
  for_each = local.tts_remote_models

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.tts[each.key].name}/variant/primary"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.tts_min_instances
  max_capacity       = var.tts_max_instances
}

resource "aws_appautoscaling_policy" "tts_invocations" {
  for_each = local.tts_remote_models

  name               = "${each.value.endpoint_name}-invocations"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.tts[each.key].service_namespace
  resource_id        = aws_appautoscaling_target.tts[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.tts[each.key].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = 20
    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
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
