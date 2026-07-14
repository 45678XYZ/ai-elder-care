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
# TODO: rest api、authorizer、resources/methods、lambda integrations、deployment/stage
