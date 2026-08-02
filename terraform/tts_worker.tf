# 非同步 TTS：合成佇列、worker Lambda 與最小 IAM 權限。
#
# 合成搬出 chat 同步路徑的理由是硬性的：自建模型合成一段回覆要數十秒到數分鐘，而
# POST /chat 走 API Gateway REST，整條請求上限 29 秒。在同步路徑上這些 provider 永遠
# 等不到、永遠 fallback，而且逾時不會取消——SageMaker 仍會把那段推論做完，於是每個逾時
# 的請求都在序列化的 endpoint 上多排一份沒人收得到的工作。
#
# chat 只負責入列並立刻回文字；worker 用 Lambda 能跑 15 分鐘的預算把 MP3 寫進 S3，
# 再把 key 補回 turn。契約見 docs/api.md 的 reply_audio_status。

# 重試次數同時是佇列的 redrive 設定與 worker 的判斷依據，因此只留一份。
# worker 靠它認出「這是最後一次投遞」，好在訊息掉進 DLQ 之前把 turn 的 pending 標記
# 收乾淨（見 backend/src/handlers/tts_worker.py 的 _is_final_attempt）。
locals {
  tts_max_receive_count = 2
}

resource "aws_sqs_queue" "tts_dlq" {
  name = "${var.project_name}-tts-dlq"

  # 保留久一點，人工排查與 replay 前要有時間。這條佇列沒有 consumer，
  # 因此不受「visibility timeout 不得小於 Lambda timeout」的限制。
  message_retention_seconds = 1209600 # 14 天
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "tts" {
  name = "${var.project_name}-tts"

  # visibility timeout 必須 >= Lambda timeout，否則同一段合成會在處理中被重投，
  # 而重投的代價在這裡是好幾分鐘的 GPU 時間。
  #
  # 這裡不像 batch 佇列取 6 倍：worker 的 timeout 本來就長，再乘 6 會逼近甚至超過下面的
  # 保留期，訊息等不到第二次投遞就過期了。只留一分鐘緩衝給 Lambda 收尾。
  visibility_timeout_seconds = var.tts_worker_timeout + 60

  # 保留期短：語音是對話當下的東西，隔一小時才合成出來對長者已經沒有意義，
  # 留著只會在故障恢復後灌一批沒人要聽的音訊進 endpoint。
  message_retention_seconds = 3600 # 1 小時
  sqs_managed_sse_enabled   = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.tts_dlq.arn
    # 合成很貴，重試次數壓低：provider 暫時性故障靠這兩次吸收，再失敗就讓它進 DLQ，
    # 不要用整個 GPU 佇列去換一段長者其實已經看得到文字的音訊。
    maxReceiveCount = local.tts_max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "tts_dlq" {
  queue_url = aws_sqs_queue.tts_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.tts.arn]
  })
}

module "tts_worker" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "~> 8.0"

  function_name = "${var.project_name}-tts-worker"
  description   = "非同步 TTS 合成 worker（SQS consumer）"
  handler       = "src.handlers.tts_worker.handler"
  runtime       = "python3.13"
  timeout       = var.tts_worker_timeout
  memory_size   = 512

  create_role = false
  lambda_role = aws_iam_role.lambda_backend_role.arn

  # 包由 module.backend_package 統一產出（見 lambda.tf 上方說明），這裡只指向它
  create_package      = false
  s3_existing_package = local.backend_package

  architectures = local.lambda_architectures

  cloudwatch_logs_retention_in_days = 30

  environment_variables = {
    S3_AUDIO_BUCKET     = "${var.project_name}-audio"
    TABLE_CONVERSATIONS = aws_dynamodb_table.conversations.name

    ASR_CONFIG_SSM_PARAMETER = aws_ssm_parameter.asr_config.name
    TTS_CONFIG_SSM_PARAMETER = aws_ssm_parameter.tts_config.name
    ASR_CONFIG_VERSION       = tostring(aws_ssm_parameter.asr_config.version)
    TTS_CONFIG_VERSION       = tostring(aws_ssm_parameter.tts_config.version)

    # worker 的意義就是「等得起」：這裡要真的等 SageMaker 做完，而不是像 chat 那樣
    # 快速失敗。留 60 秒給函數自己收尾，避免 Lambda 先被砍導致訊息重投、整段重做。
    TTS_SAGEMAKER_READ_TIMEOUT_SECONDS = tostring(var.tts_worker_timeout - 60)
    TTS_WORKER_BUDGET_SECONDS          = tostring(var.tts_worker_timeout - 60)

    # 必須與上面佇列的 redrive_policy 同值，worker 才認得出最後一次投遞。
    TTS_MAX_RECEIVE_COUNT = tostring(local.tts_max_receive_count)
  }
}

resource "aws_lambda_event_source_mapping" "tts_worker" {
  event_source_arn        = aws_sqs_queue.tts.arn
  function_name           = module.tts_worker.lambda_function_arn
  function_response_types = ["ReportBatchItemFailures"]

  # 一次一則：endpoint 端本來就是序列化的，批次拿多則只會讓後面幾則佔著 visibility
  # timeout 空等，反而更容易被重投。
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0

  # 同時最多兩個 worker。endpoint 只有一台且容器內以 lock 串行化，開更多併發不會更快，
  # 只會讓請求堆在容器裡等待、把 SageMaker 端的延遲拉長到看起來像故障。
  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_iam_policy" "tts_queue_access" {
  name        = "${var.project_name}-tts-queue-access"
  description = "Chat Lambda 送出合成工作、TTS worker 消費該佇列"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.tts.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.tts.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "tts_queue_access" {
  role       = aws_iam_role.lambda_backend_role.name
  policy_arn = aws_iam_policy.tts_queue_access.arn
}
