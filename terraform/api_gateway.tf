# API Gateway（REST，路徑前綴 /v1，Cognito JWT authorizer）
#
# 路由 → Lambda 對應（規格見 docs/api.md 端點總覽）：
#   POST  /chat                                             → chat ✅
#   GET/POST /elders、GET/PATCH /elders/{elder_id}          → elders ✅
#   GET   /summaries、POST /summaries/generate              → summaries（feature/daily-summaries 實作）
#   GET   /events                                           → events（feature/daily-summaries 實作）
#   GET/POST /routines、PATCH /routines/{routine_id}、
#   POST  /routines/{routine_id}/complete                   → routines（待其他分支實作）
#   GET   /stats                                            → stats（待其他分支實作）
#
# 新增路由的固定四件事：
#   1. aws_api_gateway_resource：路徑節點
#   2. aws_api_gateway_method：authorization = "COGNITO_USER_POOLS" + authorizer_id
#   3. aws_api_gateway_integration：AWS_PROXY、integration_http_method 一律 POST
#   4. aws_lambda_permission：source_arn 收斂到該 method
#
# 授權一律在 handler 內做細粒度判斷（見 backend/src/shared/auth.py）

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

# =============================================================================
# 模組 A 路由：POST /chat + GET|POST /elders + GET|PATCH /elders/{elder_id}
# =============================================================================

# --- POST /chat（語音對話核心）---

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "chat"
}

resource "aws_api_gateway_method" "post_chat" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.chat.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "post_chat" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.post_chat.http_method

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

# --- GET /elders + POST /elders（列表查詢與新建）---

resource "aws_api_gateway_resource" "elders" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "elders"
}

resource "aws_api_gateway_method" "get_elders" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.elders.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "get_elders" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.elders.id
  http_method = aws_api_gateway_method.get_elders.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_api_gateway_method" "post_elders" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.elders.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "post_elders" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.elders.id
  http_method = aws_api_gateway_method.post_elders.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_lambda_permission" "apigw_elders" {
  statement_id  = "AllowApiGatewayElders"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  # 允許 GET 與 POST 兩個 method 共用一個 permission（wildcard method）
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/elders"
}

resource "aws_lambda_permission" "apigw_post_elders" {
  statement_id  = "AllowApiGatewayPostElders"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/elders"
}

# --- GET /elders/{elder_id} + PATCH /elders/{elder_id}（單筆查詢與更新）---

resource "aws_api_gateway_resource" "elder_id" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.elders.id
  path_part   = "{elder_id}"
}

resource "aws_api_gateway_method" "get_elder" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.elder_id.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.elder_id" = true
  }
}

resource "aws_api_gateway_integration" "get_elder" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.elder_id.id
  http_method = aws_api_gateway_method.get_elder.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_api_gateway_method" "patch_elder" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.elder_id.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.elder_id" = true
  }
}

resource "aws_api_gateway_integration" "patch_elder" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.elder_id.id
  http_method = aws_api_gateway_method.patch_elder.http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.elders.invoke_arn
}

resource "aws_lambda_permission" "apigw_get_elder" {
  statement_id  = "AllowApiGatewayGetElder"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/elders/*"
}

resource "aws_lambda_permission" "apigw_patch_elder" {
  statement_id  = "AllowApiGatewayPatchElder"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.elders.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/PATCH/elders/*"
}

# =============================================================================
# API Gateway Deployment 與 Stage（v1）
# 注意：deployment triggers 需涵蓋所有 method/integration，漏掉不會重新部署
# =============================================================================

resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeployment = sha1(jsonencode([
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
      aws_api_gateway_authorizer.cognito,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.post_chat,
    aws_api_gateway_integration.get_elders,
    aws_api_gateway_integration.post_elders,
    aws_api_gateway_integration.get_elder,
    aws_api_gateway_integration.patch_elder,
  ]
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.project_name}-api"
  retention_in_days = 30
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = "v1"

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
