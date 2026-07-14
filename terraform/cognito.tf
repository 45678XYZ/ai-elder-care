# Cognito（長者／照護者帳號與角色）
#
# 註冊／登入由 App 直接走 Cognito SDK，不經 API Gateway；
# ID token 供 API Gateway authorizer 驗證，長者帳號帶 elder_id claim。
#
# TODO: user pool、app client、群組或 custom claim（角色與 elder_id）
