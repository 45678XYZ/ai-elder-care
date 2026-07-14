"""授權：呼叫者身分一律取自 Cognito JWT claims，不以參數傳遞。

- 長者 token 帶 elder_id claim，只能存取自己的資料
- 照護者可存取 elders.caregiver_ids 綁定的所有長者（POST /elders 時建立者自動綁定）
- 越權回 403 FORBIDDEN
"""


def get_caller(event):
    """從 API Gateway authorizer claims 取出呼叫者身分（角色與 id）。"""
    raise NotImplementedError  # TODO


def assert_can_access_elder(event, elder_id: str):
    """驗證呼叫者可存取指定長者，否則拋出 403。所有端點共用。"""
    raise NotImplementedError  # TODO
