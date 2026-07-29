# API Gateway（REST，路徑前綴 /v1，Cognito JWT authorizer）
#
# 路由 → Lambda 對應（規格見 docs/api.md 端點總覽）：
#   POST  /chat                                             → chat（待實作）
#   POST  /chat/sessions/{session_id}/close                 → session_closer ✅
#   GET/POST /elders、GET/PATCH /elders/{elder_id}          → elders（待實作）
#   GET   /summaries、POST /summaries/generate              → api_summaries ✅
#   GET   /events                                           → events ✅
#   GET/POST /routines、PATCH /routines/{routine_id}、
#   POST  /routines/{routine_id}/complete                   → routines（待實作）
#   GET   /stats                                            → stats（待實作）
#
# 各模組自行補自己的路由，共用地基（REST API、authorizer、deployment、stage、
# 存取日誌、節流）在此檔一次備妥。新增路由的固定四件事：
#   1. aws_api_gateway_resource：路徑節點
#   2. aws_api_gateway_method：authorization = "COGNITO_USER_POOLS" + authorizer_id
#   3. aws_api_gateway_integration：AWS_PROXY、integration_http_method 一律 POST
#   4. aws_lambda_permission：source_arn 收斂到該 method，並把 method 加入
#      aws_api_gateway_deployment 的 triggers，否則改動不會重新部署
#
# 授權一律在 handler 內做細粒度判斷（見 backend/src/shared/auth.py）：authorizer 只保證
# token 有效，「這個呼叫者能不能碰這個長者」由 assert_can_access_elder 依 elders.caregiver_ids
# 決定；close endpoint 另有「不存在與越權都回 404」的防洩漏規則。

resource "aws_api_gateway_rest_api" "api" {
  name = "${var.project_name}-api"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# 各端點的 method 掛此 authorizer（authorization = "COGNITO_USER_POOLS"、
# authorizer_id = 此資源）；驗證通過後 claims 進 event.requestContext.authorizer.claims，
# 供 backend/src/shared/auth.py 讀取。
resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito"
  type            = "COGNITO_USER_POOLS"
  rest_api_id     = aws_api_gateway_rest_api.api.id
  provider_arns   = [aws_cognito_user_pool.accounts.arn]
  identity_source = "method.request.header.Authorization"
}

# 請求格式驗證器：在最前線檢查必填 Querystring / Headers 與 Request Body 格式，
# 無效請求在 Gateway 直接擋下 (400)，不觸發後端 Lambda 節省算力與費用。
resource "aws_api_gateway_request_validator" "validator" {
  name                        = "${var.project_name}-request-validator"
  rest_api_id                 = aws_api_gateway_rest_api.api.id
  validate_request_body       = true
  validate_request_parameters = true
}

# --- GET /events（照護者事件時間軸）---

resource "aws_api_gateway_resource" "events" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "events"
}

resource "aws_api_gateway_method" "get_events" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.events.id
  http_method          = "GET"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id

  request_parameters = {
    "method.request.querystring.elder_id"   = true
    "method.request.querystring.from"       = false
    "method.request.querystring.to"         = false
    "method.request.querystring.type"       = false
    "method.request.querystring.limit"      = false
    "method.request.querystring.next_token" = false
  }
}

resource "aws_api_gateway_integration" "get_events" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.events.id
  http_method = aws_api_gateway_method.get_events.http_method

  # proxy 整合一律以 POST 呼叫 Lambda，與對外的 HTTP method 無關
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_events.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "get_events" {
  statement_id  = "AllowApiGatewayGetEvents"
  action        = "lambda:InvokeFunction"
  function_name = module.api_events.lambda_function_name
  principal     = "apigateway.amazonaws.com"

  # 收斂到具體 method，避免整個 API 都能叫這支 Lambda
  source_arn = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/events"
}

# --- GET /summaries、POST /summaries/generate（照護者每日摘要）---
# 兩條路由掛同一支 Lambda，handler 內依 httpMethod 分派。

resource "aws_api_gateway_resource" "summaries" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "summaries"
}

resource "aws_api_gateway_method" "get_summaries" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.summaries.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.querystring.elder_id"   = true
    "method.request.querystring.from"       = false
    "method.request.querystring.to"         = false
    "method.request.querystring.limit"      = false
    "method.request.querystring.next_token" = false
  }
}

resource "aws_api_gateway_integration" "get_summaries" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.summaries.id
  http_method = aws_api_gateway_method.get_summaries.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.api_summaries.invoke_arn
}

resource "aws_api_gateway_resource" "summaries_generate" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.summaries.id
  path_part   = "generate"
}

resource "aws_api_gateway_method" "generate_summary" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.summaries_generate.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "generate_summary" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.summaries_generate.id
  http_method = aws_api_gateway_method.generate_summary.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.api_summaries.invoke_arn
}

# 一支 Lambda 兩條路由，因此兩個 permission 各自收斂到自己的 method
resource "aws_lambda_permission" "get_summaries" {
  statement_id  = "AllowApiGatewayGetSummaries"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_summaries.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/summaries"
}

resource "aws_lambda_permission" "generate_summary" {
  statement_id  = "AllowApiGatewayGenerateSummary"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_summaries.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/summaries/generate"
}

# --- POST /chat/sessions/{session_id}/close（長者端明確關閉）---

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "chat"
}

resource "aws_api_gateway_resource" "chat_sessions" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.chat.id
  path_part   = "sessions"
}

resource "aws_api_gateway_resource" "chat_session" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.chat_sessions.id
  path_part   = "{session_id}"
}

resource "aws_api_gateway_resource" "chat_session_close" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.chat_session.id
  path_part   = "close"
}

resource "aws_api_gateway_method" "close_session" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.chat_session_close.id
  http_method          = "POST"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id

  request_parameters = {
    "method.request.path.session_id" = true
  }
}

resource "aws_api_gateway_integration" "close_session" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.chat_session_close.id
  http_method = aws_api_gateway_method.close_session.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.session_closer.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "close_session" {
  statement_id  = "AllowApiGatewayCloseSession"
  action        = "lambda:InvokeFunction"
  function_name = module.session_closer.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/chat/sessions/*/close"
}

# --- deployment 與 stage ---

resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  # triggers 要涵蓋所有 method／integration；漏掉的話改了設定不會重新部署，
  # 而 API Gateway 的舊快照會繼續生效，是很難察覺的錯誤
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_method.get_events,
      aws_api_gateway_integration.get_events,
      aws_api_gateway_method.get_summaries,
      aws_api_gateway_integration.get_summaries,
      aws_api_gateway_method.generate_summary,
      aws_api_gateway_integration.generate_summary,
      aws_api_gateway_method.close_session,
      aws_api_gateway_integration.close_session,
      aws_api_gateway_authorizer.cognito,
      aws_api_gateway_request_validator.validator,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.project_name}-api"
  retention_in_days = 30
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = "v1"

  # 存取日誌不含 request body，避免逐字稿與 PII 落到日誌
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      latency        = "$context.responseLatency"
      integrationErr = "$context.integrationErrorMessage"
      requestTime    = "$context.requestTime"
    })
  }
}

resource "aws_api_gateway_method_settings" "v1" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  method_path = "*/*"

  settings {
    # 長者端是免手持語音迴圈，單一裝置的請求頻率有限；節流是防呆與成本上限，
    # 不是效能調校。實際值待壓測後調整。
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit

    metrics_enabled = true
    logging_level   = "ERROR"
  }
}
