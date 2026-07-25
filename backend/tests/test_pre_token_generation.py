"""pre_token_generation trigger 單元測試：發 token 前依對應表注入 elder_id claim。"""
from src.handlers import pre_token_generation as trigger


def _event(sub="usr_1"):
    return {
        "request": {"userAttributes": {"sub": sub}},
        "response": {"claimsOverrideDetails": None},
    }


def test_injects_elder_id_for_mapped_account(monkeypatch):
    monkeypatch.setattr(trigger, "_lookup_elder_id", lambda sub: "eld_001")
    out = trigger.handler(_event("usr_elder"), None)
    assert out["response"]["claimsOverrideDetails"] == {
        "claimsToAddOrOverride": {"elder_id": "eld_001"}
    }


def test_no_claim_for_unmapped_account(monkeypatch):
    # 照護者帳號：對應表查無 → 不注入，token 無 elder_id claim
    monkeypatch.setattr(trigger, "_lookup_elder_id", lambda sub: None)
    out = trigger.handler(_event("usr_caregiver"), None)
    assert out["response"]["claimsOverrideDetails"] is None


def test_missing_sub_does_not_query(monkeypatch):
    def _boom(sub):
        raise AssertionError("無 sub 不應查表")

    monkeypatch.setattr(trigger, "_lookup_elder_id", _boom)
    event = {"request": {"userAttributes": {}}, "response": {"claimsOverrideDetails": None}}
    out = trigger.handler(event, None)
    assert out["response"]["claimsOverrideDetails"] is None
