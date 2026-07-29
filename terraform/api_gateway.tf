# API Gateway（REST，路徑前綴 /v1，Cognito JWT authorizer）
#
# 路由 → Lambda 對應（規格見 docs/api.md 端點總覽）：
#   POST  /chat                                             → chat ✅
#   POST  /chat/sessions/{session_id}/close                 → session_closer ✅
#   GET/POST /elders、GET/PATCH /elders/{elder_id}          → elders ✅
#   GET   /summaries、POST /summaries/generate              → summaries（feature/daily-summaries 實作）
#   GET   /events                                           → events ✅
#   GET/POST /routines、PATCH /routines/{routine_id}、
#   POST  /routines/{routine_id}/complete                   → routines（待其他分支實作）
#   GET   /stats                                            → stats（待其他分支實作）
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

# Gateway 產生的錯誤不經過 Lambda，預設格式是 {"message": ...}，沒有前端分支用的 code。
# code 取 $context.error.responseType（THROTTLED、UNAUTHORIZED、INTEGRATION_TIMEOUT…）
locals {
  gateway_error_template = {
    "application/json" = "{\"error\":{\"code\":\"$context.error.responseType\",\"message\":$context.error.messageString}}"
  }
}

resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id        = aws_api_gateway_rest_api.api.id
  response_type      = "DEFAULT_4XX"
  response_templates = local.gateway_error_template
}

resource "aws_api_gateway_gateway_response" "default_5xx" {
  rest_api_id        = aws_api_gateway_rest_api.api.id
  response_type      = "DEFAULT_5XX"
  response_templates = local.gateway_error_template
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
    "method.request.querystring.elder_id"   = false
    "method.request.querystring.from"       = false
    "method.request.querystring.to"         = false
    "method.request.querystring.type"       = false
    "method.request.querystring.limit"      = false
    "method.request.querystring.next_token" = false
  }
}

resource "aws_api_gateway_integration" "get_events" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.events.id
  http_method             = aws_api_gateway_method.get_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_events.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "get_events" {
  statement_id  = "AllowApiGatewayGetEvents"
  action        = "lambda:InvokeFunction"
  function_name = module.api_events.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/events"
}

# --- POST /chat + GET|POST /elders + GET|PATCH /elders/{elder_id} ---

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "chat"
}

resource "aws_api_gateway_method" "post_chat" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.chat.id
  http_method          = "POST"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
}

resource "aws_api_gateway_integration" "post_chat" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.chat.id
  http_method             = aws_api_gateway_method.post_chat.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_chat.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "apigw_post_chat" {
  statement_id  = "AllowApiGatewayPostChat"
  action        = "lambda:InvokeFunction"
  function_name = module.api_chat.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/chat"
}

# --- GET /elders + POST /elders（列表查詢與新建）---

resource "aws_api_gateway_resource" "elders" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "elders"
}

resource "aws_api_gateway_method" "get_elders" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.elders.id
  http_method          = "GET"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
}

resource "aws_api_gateway_integration" "get_elders" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.elders.id
  http_method             = aws_api_gateway_method.get_elders.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_elders.lambda_function_invoke_arn
}

resource "aws_api_gateway_method" "post_elders" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.elders.id
  http_method          = "POST"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
}

resource "aws_api_gateway_integration" "post_elders" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.elders.id
  http_method             = aws_api_gateway_method.post_elders.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_elders.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "apigw_elders" {
  statement_id  = "AllowApiGatewayElders"
  action        = "lambda:InvokeFunction"
  function_name = module.api_elders.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*/elders"
}

# --- GET /elders/{elder_id} + PATCH /elders/{elder_id}（單筆查詢與更新）---

resource "aws_api_gateway_resource" "elder_id" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.elders.id
  path_part   = "{elder_id}"
}

resource "aws_api_gateway_method" "get_elder" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.elder_id.id
  http_method          = "GET"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
  request_parameters = {
    "method.request.path.elder_id" = true
  }
}

resource "aws_api_gateway_integration" "get_elder" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.elder_id.id
  http_method             = aws_api_gateway_method.get_elder.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_elders.lambda_function_invoke_arn
}

resource "aws_api_gateway_method" "patch_elder" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.elder_id.id
  http_method          = "PATCH"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
  request_parameters = {
    "method.request.path.elder_id" = true
  }
}

resource "aws_api_gateway_integration" "patch_elder" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.elder_id.id
  http_method             = aws_api_gateway_method.patch_elder.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_elders.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "apigw_elder_ops" {
  statement_id  = "AllowApiGatewayElderOps"
  action        = "lambda:InvokeFunction"
  function_name = module.api_elders.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/elders/*"
}

# --- POST /chat/sessions/{session_id}/close（長者端明確關閉）---

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
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.chat_session_close.id
  http_method             = aws_api_gateway_method.close_session.http_method
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

# =============================================================================
# API Gateway Deployment 與 Stage（v1）
# =============================================================================

resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  # triggers 要涵蓋所有 method／integration；漏掉的話改了設定不會重新部署，
  # 而 API Gateway 的舊快照會繼續生效，是很難察覺的錯誤
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_method.get_events,
      aws_api_gateway_integration.get_events,
      aws_api_gateway_method.post_chat,
      aws_api_gateway_integration.post_chat,
      aws_api_gateway_method.get_elders,
      aws_api_gateway_integration.get_elders,
      aws_api_gateway_method.post_elders,
      aws_api_gateway_integration.post_elders,
      aws_api_gateway_method.get_elder,
      aws_api_gateway_integration.get_elder,
      aws_api_gateway_method.patch_elder,
      aws_api_gateway_integration.patch_elder,
      aws_api_gateway_method.close_session,
      aws_api_gateway_integration.close_session,
      aws_api_gateway_authorizer.cognito,
      aws_api_gateway_request_validator.validator,
      aws_api_gateway_gateway_response.default_4xx,
      aws_api_gateway_gateway_response.default_5xx,
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
    # 長者端是免手持語音迴圈，單一裝置的請求頻率有限；節流是防呆與成本上限。
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit
    metrics_enabled = true
    logging_level   = "ERROR"
  }
}
