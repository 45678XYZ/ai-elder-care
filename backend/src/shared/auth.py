"""授權：呼叫者身分一律取自 Cognito JWT claims，不以參數傳遞。

- 長者 token 帶 elder_id claim，只能存取自己的資料
- 照護者可存取 elders.caregiver_ids 綁定的所有長者（POST /elders 時建立者自動綁定）
- 越權回 403 FORBIDDEN

用法：handler 呼叫 `caller = auth.assert_can_access_elder(event, elder_id)` 授權並取得
呼叫者身分；捕捉 AuthError 後直接回傳其 `response`（錯誤格式見 docs/api.md）。
"""
from dataclasses import dataclass

from src.shared import db, responses

ROLE_ELDER = "elder"
ROLE_CAREGIVER = "caregiver"

# 長者身分以 elder_id claim 判定。Cognito 自訂屬性在 ID token 內會帶 `custom:` 前綴，
# 兩種設定皆接受；對外契約以 api.md 的 elder_id 為準。
_ELDER_ID_CLAIMS = ("elder_id", "custom:elder_id")


@dataclass(frozen=True)
class Caller:
    """呼叫者身分。user_id 為 Cognito sub，即照護者綁定於 elders.caregiver_ids 的值。"""

    role: str
    user_id: str | None
    elder_id: str | None  # 長者呼叫者綁定的 elder_id；照護者為 None


class AuthError(Exception):
    """授權失敗，attach 對應的 HTTP 錯誤回應；handler 捕捉後直接回傳 self.response。"""

    def __init__(self, response: dict):
        super().__init__(response["body"])
        self.response = response


def get_caller(event) -> Caller:
    """從 API Gateway authorizer claims 取出呼叫者身分（角色與 id）。"""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims") or {}
    # 通過驗證的請求必帶 claims（否則 API Gateway 於閘道回 401）；走到這裡仍為空，
    # 代表 Cognito authorizer 未正確套用，屬伺服器設定問題。
    if not claims:
        raise AuthError(responses.error(500, "INTERNAL_ERROR", "缺少授權資訊"))

    elder_id = _first_claim(claims, _ELDER_ID_CLAIMS)
    role = ROLE_ELDER if elder_id else ROLE_CAREGIVER
    return Caller(role=role, user_id=claims.get("sub"), elder_id=elder_id)


def assert_can_access_elder(event, elder_id: str) -> Caller:
    """驗證呼叫者可存取指定長者，否則拋出 AuthError。所有端點共用，回傳呼叫者身分。"""
    caller = get_caller(event)

    # 長者：token 即身分來源，只能存取自己，不需查表。
    if caller.role == ROLE_ELDER:
        if caller.elder_id != elder_id:
            raise _forbidden()
        return caller

    # 照護者：依 elders.caregiver_ids 綁定關係授權。
    elder = db.get_elder(elder_id)
    # 長者不存在時回 404（而非 403）以符合 api.md 錯誤契約；照護者為受信任的少數，
    # 是否存在非敏感資訊。exists-but-unbound 才回 403。
    if elder is None:
        raise AuthError(responses.error(404, "ELDER_NOT_FOUND", "找不到指定的長者"))
    if caller.user_id not in elder.get("caregiver_ids", []):
        raise _forbidden()
    return caller


def _first_claim(claims: dict, keys):
    """回傳第一個存在且非空的 claim 值，皆無則回 None。"""
    for key in keys:
        value = claims.get(key)
        if value:
            return value
    return None


def _forbidden() -> AuthError:
    return AuthError(responses.error(403, "FORBIDDEN", "無權存取此長者的資料"))
