# API Gateway（REST，路徑前綴 /v1，Cognito JWT authorizer）
#
# 路由 → Lambda 對應（規格見 docs/api.md 端點總覽）：
#   POST  /chat                         → chat
#   GET/POST /elders、GET/PATCH /elders/{elder_id}          → elders
#   GET   /summaries、POST /summaries/generate              → summaries
#   GET   /events                       → events
#   GET/POST /routines、PATCH /routines/{routine_id}、
#   POST  /routines/{routine_id}/complete                   → routines
#   GET   /stats                        → stats
#
# 目前只立 REST API 本體與 Cognito authorizer（auth 相關骨架）；各路由的
# resources/methods/integrations 由各 module 補上，deployment/stage 待有 method 後建。

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

# TODO（各 module／共用地基）：resources/methods/integrations、deployment、stage（/v1）
