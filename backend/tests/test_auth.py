"""src.shared.auth 單元測試：身分解析與長者存取授權。"""
import json

import pytest

from src.shared import auth, db


def _event(claims):
    """組出帶 Cognito authorizer claims 的 API Gateway 事件。"""
    return {"requestContext": {"authorizer": {"claims": claims}}}


def _elder_event(elder_id, sub="usr_elder"):
    return _event({"sub": sub, "elder_id": elder_id})


def _caregiver_event(sub="usr_care"):
    return _event({"sub": sub})


def _error(exc):
    """從 AuthError 的回應取出 (statusCode, code)。"""
    err = exc.value
    return err.response["statusCode"], json.loads(err.response["body"])["error"]["code"]


# --- get_caller ---


def test_get_caller_elder():
    caller = auth.get_caller(_elder_event("eld_001", sub="usr_e1"))
    assert caller.role == auth.ROLE_ELDER
    assert caller.elder_id == "eld_001"
    assert caller.user_id == "usr_e1"


def test_get_caller_caregiver():
    caller = auth.get_caller(_caregiver_event(sub="usr_c1"))
    assert caller.role == auth.ROLE_CAREGIVER
    assert caller.elder_id is None
    assert caller.user_id == "usr_c1"


def test_get_caller_accepts_custom_prefixed_elder_id_claim():
    caller = auth.get_caller(_event({"sub": "usr_e1", "custom:elder_id": "eld_009"}))
    assert caller.role == auth.ROLE_ELDER
    assert caller.elder_id == "eld_009"


def test_get_caller_missing_claims_is_500():
    with pytest.raises(auth.AuthError) as exc:
        auth.get_caller({"requestContext": {}})
    assert _error(exc) == (500, "INTERNAL_ERROR")


# --- assert_can_access_elder ---


def test_elder_can_access_own(monkeypatch):
    # 長者自存取不需查表：get_elder 若被呼叫即讓測試失敗。
    monkeypatch.setattr(db, "get_elder", lambda _id: pytest.fail("長者自存取不應查表"))
    caller = auth.assert_can_access_elder(_elder_event("eld_001"), "eld_001")
    assert caller.role == auth.ROLE_ELDER


def test_elder_cannot_access_other():
    with pytest.raises(auth.AuthError) as exc:
        auth.assert_can_access_elder(_elder_event("eld_001"), "eld_002")
    assert _error(exc) == (403, "FORBIDDEN")


def test_caregiver_bound_can_access(monkeypatch):
    monkeypatch.setattr(
        db, "get_elder", lambda _id: {"elder_id": _id, "caregiver_ids": ["usr_c1"]}
    )
    caller = auth.assert_can_access_elder(_caregiver_event("usr_c1"), "eld_001")
    assert caller.role == auth.ROLE_CAREGIVER
    assert caller.user_id == "usr_c1"


def test_caregiver_unbound_is_403(monkeypatch):
    monkeypatch.setattr(
        db, "get_elder", lambda _id: {"elder_id": _id, "caregiver_ids": ["usr_other"]}
    )
    with pytest.raises(auth.AuthError) as exc:
        auth.assert_can_access_elder(_caregiver_event("usr_c1"), "eld_001")
    assert _error(exc) == (403, "FORBIDDEN")


def test_caregiver_missing_elder_is_404(monkeypatch):
    monkeypatch.setattr(db, "get_elder", lambda _id: None)
    with pytest.raises(auth.AuthError) as exc:
        auth.assert_can_access_elder(_caregiver_event("usr_c1"), "eld_404")
    assert _error(exc) == (404, "ELDER_NOT_FOUND")
