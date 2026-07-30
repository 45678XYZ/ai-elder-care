# API Gateway嚗EST嚗楝敺?蝬?/v1嚗ognito JWT authorizer嚗?
#
# 頝舐 ??Lambda 撠?嚗??潸? docs/api.md 蝡舫?蝮質汗嚗?
#   POST  /chat                                             ??chat ??
#   POST  /chat/sessions/{session_id}/close                 ??session_closer ??
#   GET/POST /elders?ET/PATCH /elders/{elder_id}          ??elders ??
#   GET   /summaries?OST /summaries/generate              ??summaries嚗eature/daily-summaries 撖虫?嚗?
#   GET   /events                                           ??events ??
#   GET/POST /routines?ATCH /routines/{routine_id}??
#   POST  /routines/{routine_id}/complete                   ??routines嚗??嗡??撖虫?嚗?
#   GET   /stats                                            → stats
#
# ?芋蝯銵??芸楛?楝?梧??梁?啣嚗EST API?uthorizer?eployment?tage??
# 摮??亥???瘚??冽迨瑼?甈∪?憒乓憓楝?梁??箏??辣鈭?
#   1. aws_api_gateway_resource嚗楝敺?暺?
#   2. aws_api_gateway_method嚗uthorization = "COGNITO_USER_POOLS" + authorizer_id
#   3. aws_api_gateway_integration嚗WS_PROXY?ntegration_http_method 銝敺?POST
#   4. aws_lambda_permission嚗ource_arn ?嗆??啗府 method嚗蒂??method ?
#      aws_api_gateway_deployment ??triggers嚗??????圈蝵?
#
# ??銝敺 handler ?批?蝝啁?摨血?瘀?閬?backend/src/shared/auth.py嚗?authorizer ?芯?霅?
# token ??嚗?怨銝蝣圈? assert_can_access_elder 靘?elders.caregiver_ids
# 瘙箏?嚗lose endpoint ?行???摮??甈??404???脫援瞍???

resource "aws_api_gateway_rest_api" "api" {
  name = "${var.project_name}-api"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# ?垢暺? method ?迨 authorizer嚗uthorization = "COGNITO_USER_POOLS"??
# authorizer_id = 甇方?皞?嚗?霅?敺?claims ??event.requestContext.authorizer.claims嚗?
# 靘?backend/src/shared/auth.py 霈??
resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito"
  type            = "COGNITO_USER_POOLS"
  rest_api_id     = aws_api_gateway_rest_api.api.id
  provider_arns   = [aws_cognito_user_pool.accounts.arn]
  identity_source = "method.request.header.Authorization"
}

# 隢??澆?撽??剁??冽???瑼Ｘ敹‵ Querystring / Headers ??Request Body ?澆?嚗?
# ?⊥?隢???Gateway ?湔?? (400)嚗?閫貊敺垢 Lambda 蝭????鞎餌??
resource "aws_api_gateway_request_validator" "validator" {
  name                        = "${var.project_name}-request-validator"
  rest_api_id                 = aws_api_gateway_rest_api.api.id
  validate_request_body       = true
  validate_request_parameters = true
}

# Gateway ?Ｙ??隤支?蝬? Lambda嚗?閮剜撘 {"message": ...}嚗???蝡臬??舐??code??
# code ??$context.error.responseType嚗HROTTLED?NAUTHORIZED?NTEGRATION_TIMEOUT?佗?
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

# --- GET /events嚗霅瑁?隞嗆??遘嚗?--

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

# --- GET /stats（互動與行程統計）---

resource "aws_api_gateway_resource" "stats" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "stats"
}

resource "aws_api_gateway_method" "get_stats" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.stats.id
  http_method          = "GET"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  request_validator_id = aws_api_gateway_request_validator.validator.id
  request_parameters = {
    "method.request.querystring.elder_id" = false
    "method.request.querystring.days"     = false
  }
}

resource "aws_api_gateway_integration" "get_stats" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.stats.id
  http_method             = aws_api_gateway_method.get_stats.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = module.api_stats.lambda_function_invoke_arn
}

resource "aws_lambda_permission" "get_stats" {
  statement_id  = "AllowApiGatewayGetStats"
  action        = "lambda:InvokeFunction"
  function_name = module.api_stats.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/stats"
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
  uri                     = aws_lambda_function.chat.invoke_arn
}

resource "aws_lambda_permission" "apigw_post_chat" {
  statement_id  = "AllowApiGatewayPostChat"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.chat.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/chat"
}

# --- GET /elders + POST /elders嚗?銵冽閰Ｚ??啣遣嚗?--

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
  uri                     = aws_lambda_function.elders.invoke_arn
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
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_lambda_permission" "apigw_elders" {
  statement_id  = "AllowApiGatewayElders"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*/elders"
}

# --- GET /elders/{elder_id} + PATCH /elders/{elder_id}嚗蝑閰Ｚ??湔嚗?--

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
  uri                     = aws_lambda_function.elders.invoke_arn
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
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_lambda_permission" "apigw_elder_ops" {
  statement_id  = "AllowApiGatewayElderOps"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/elders/*"
}

# --- POST /chat/sessions/{session_id}/close嚗?垢?Ⅱ??嚗?--

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
# API Gateway Deployment ??Stage嚗1嚗?
# =============================================================================

resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  # triggers 閬項????method嚗ntegration嚗???閰望鈭身摰????圈蝵莎?
  # ??API Gateway ??敹怎?匱蝥????臬????閬箇??航炊
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_method.get_events,
      aws_api_gateway_integration.get_events,
      aws_api_gateway_method.get_stats,
      aws_api_gateway_integration.get_stats,
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

  # 摮??亥?銝 request body嚗??蝔輯? PII ?賢?亥?
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
    # ?瑁垢?臬???隤餈游?嚗銝鋆蔭??瘙????蝭瘚?脣????砌???
    throttling_rate_limit  = var.api_throttle_rate_limit
    throttling_burst_limit = var.api_throttle_burst_limit
    metrics_enabled = true
    logging_level   = "ERROR"
  }
}

