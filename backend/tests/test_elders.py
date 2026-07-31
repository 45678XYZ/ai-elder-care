"""src.handlers.elders 單元測試：GET/POST/PATCH /elders 長者資料管理驗證。"""

import json

import pytest

from src.handlers import elders
from src.shared import auth, db


def _make_event(method="GET", body=None, path_params=None, sub="usr_c1", role="caregiver", elder_id=None):
    claims = {"sub": sub}
    if elder_id:
        claims["elder_id"] = elder_id

    event = {
        "httpMethod": method,
        "requestContext": {
            "authorizer": {
                "claims": claims
            }
        }
    }
    if body is not None:
        event["body"] = json.dumps(body)
    if path_params:
        event["pathParameters"] = path_params
    return event


def test_get_elders_caregiver_list(monkeypatch):
    """測試照護者取得綁定長者列表。"""
    monkeypatch.setattr(
        db,
        "list_elders",
        lambda caregiver_id: [{"elder_id": "eld_001", "name": "陳阿蘭", "created_at": "2026-07-28T12:00:00+08:00"}]
    )

    event = _make_event("GET", sub="usr_c1", role="caregiver")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["items"]) == 1
    assert body["items"][0]["elder_id"] == "eld_001"


def test_get_elders_single_success(monkeypatch):
    """測試讀取單筆長者資料。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(
        db,
        "get_elder",
        lambda eid: {"elder_id": eid, "name": "陳阿蘭", "gender": "female", "created_at": "2026-07-28T12:00:00+08:00"}
    )

    event = _make_event("GET", path_params={"elder_id": "eld_001"}, sub="usr_c1")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["elder_id"] == "eld_001"
    assert body["name"] == "陳阿蘭"


def test_post_elder_server_owned_field_400():
    """測試建立長者帶入唯讀欄位回傳 400 INVALID_PARAMETER。"""
    event = _make_event("POST", body={"name": "陳阿蘭", "elder_id": "eld_hack"})
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAMETER"


def test_post_elder_missing_name_400():
    """測試建立長者未帶 name 回傳 400 INVALID_PARAMETER。"""
    event = _make_event("POST", body={"gender": "female"})
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAMETER"
    assert "name" in body["error"]["message"]


def test_post_elder_invalid_gender_400():
    """測試建立長者帶入無效 gender 回傳 400 INVALID_PARAMETER。"""
    event = _make_event("POST", body={"name": "陳阿蘭", "gender": "unknown"})
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAMETER"


def test_post_elder_success(monkeypatch):
    """測試照護者成功建立長者資料 (201 Created)。"""
    monkeypatch.setattr(
        db,
        "create_elder",
        lambda data: {
            "elder_id": "eld_1234567890ab",
            "name": data["name"],
            "caregiver_ids": data["caregiver_ids"],
            "lang_preference": data["lang_preference"],
            "created_at": "2026-07-28T12:00:00+08:00",
            "updated_at": "2026-07-28T12:00:00+08:00"
        }
    )

    event = _make_event("POST", body={"name": "陳阿蘭", "nickname": "阿蘭嬤"}, sub="usr_c1")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["elder_id"].startswith("eld_")
    assert body["caregiver_ids"] == ["usr_c1"]


def test_patch_elder_success(monkeypatch):
    """測試部分更新長者資料 (200 OK)。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(
        db,
        "update_elder",
        lambda eid, patch: {
            "elder_id": eid,
            "name": patch["name"],
            "created_at": "2026-07-28T12:00:00+08:00",
            "updated_at": "2026-07-28T12:05:00+08:00"
        }
    )

    event = _make_event("PATCH", body={"name": "陳阿蘭（更新）"}, path_params={"elder_id": "eld_001"}, sub="usr_c1")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["name"] == "陳阿蘭（更新）"

