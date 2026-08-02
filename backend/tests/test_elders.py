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


def test_post_elder_self_register_binds_account(monkeypatch):
    """長者自註冊時 self_register=true 應呼叫 bind_elder_account。"""
    bound = {}
    monkeypatch.setattr(
        db,
        "create_elder",
        lambda data: {
            "elder_id": "eld_aabbccddeeff",
            "name": data["name"],
            "caregiver_ids": data["caregiver_ids"],
            "lang_preference": "zh-TW",
            "created_at": "2026-07-28T12:00:00+08:00",
            "updated_at": "2026-07-28T12:00:00+08:00"
        }
    )
    monkeypatch.setattr(
        db,
        "bind_elder_account",
        lambda sub, eid: bound.update({"sub": sub, "elder_id": eid})
    )

    event = _make_event("POST", body={"name": "陳阿蘭", "self_register": True}, sub="usr_e1")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 201
    assert bound == {"sub": "usr_e1", "elder_id": "eld_aabbccddeeff"}


def test_post_elder_without_self_register_no_bind(monkeypatch):
    """照護者建立長者時不帶 self_register，不寫 elder_accounts。"""
    bound = {}
    monkeypatch.setattr(
        db,
        "create_elder",
        lambda data: {
            "elder_id": "eld_1234567890ab",
            "name": data["name"],
            "caregiver_ids": data["caregiver_ids"],
            "lang_preference": "zh-TW",
            "created_at": "2026-07-28T12:00:00+08:00",
            "updated_at": "2026-07-28T12:00:00+08:00"
        }
    )
    monkeypatch.setattr(
        db,
        "bind_elder_account",
        lambda sub, eid: bound.update({"sub": sub, "elder_id": eid})
    )

    event = _make_event("POST", body={"name": "陳阿蘭"}, sub="usr_c1")
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 201
    assert bound == {}


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


def test_patch_elder_elder_role_lang_allowed(monkeypatch):
    """長者本人可修改 lang_preference 和 hakka_dialect。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(
        db,
        "update_elder",
        lambda eid, patch: {
            "elder_id": eid,
            "name": "陳阿蘭",
            "lang_preference": patch.get("lang_preference", "zh-TW"),
            "hakka_dialect": patch.get("hakka_dialect", "htia_sixian"),
            "created_at": "2026-07-28T12:00:00+08:00",
            "updated_at": "2026-07-28T12:05:00+08:00"
        }
    )

    event = _make_event(
        "PATCH",
        body={"lang_preference": "hak", "hakka_dialect": "htia_hailu"},
        path_params={"elder_id": "eld_001"},
        sub="usr_e1",
        elder_id="eld_001"
    )
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["lang_preference"] == "hak"
    assert body["hakka_dialect"] == "htia_hailu"


def test_patch_elder_elder_role_other_fields_forbidden(monkeypatch):
    """長者嘗試修改非語言欄位應被 403 擋下。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)

    event = _make_event(
        "PATCH",
        body={"name": "惡意修改"},
        path_params={"elder_id": "eld_001"},
        sub="usr_e1",
        elder_id="eld_001"
    )
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 403


# =============================================================================
# /elders/{elder_id}/health_notes — 單筆增刪
# =============================================================================

def _health_note_event(method, elder_id="eld_001", note_id=None, body=None, **kwargs):
    """組出打 health_notes 子資源的事件。"""
    path_params = {"elder_id": elder_id}
    if note_id:
        path_params["note_id"] = note_id
    event = _make_event(method, body=body, path_params=path_params, **kwargs)
    event["path"] = f"/elders/{elder_id}/health_notes" + (f"/{note_id}" if note_id else "")
    return event


def test_post_health_note_success(monkeypatch):
    """新增單筆健康註記走原子 append，回 201。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    captured = {}

    def fake_append(eid, note):
        captured["note"] = note
        return {
            "elder_id": eid,
            "name": "陳阿蘭",
            "health_notes": [{"note_id": "hn_a", "text": note["text"], "source": note["source"]}],
            "created_at": "2026-07-28T12:00:00+08:00",
        }

    monkeypatch.setattr(db, "append_health_note", fake_append)

    event = _health_note_event("POST", body={"text": "膝關節退化"}, sub="usr_c1")
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["health_notes"][0]["text"] == "膝關節退化"
    # 這個端點只給照護者用，落地的來源固定 caregiver
    assert captured["note"]["source"] == "caregiver"


def test_post_health_note_rejects_client_source(monkeypatch):
    """client 不得自稱 agent，否則來源標示就沒有意義。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)

    event = _health_note_event("POST", body={"text": "膝關節退化", "source": "agent"}, sub="usr_c1")
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == "INVALID_PARAMETER"


def test_post_health_note_empty_text_400(monkeypatch):
    """text 為空回 400。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)

    event = _health_note_event("POST", body={"text": "   "}, sub="usr_c1")
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 400


def test_post_health_note_elder_role_403(monkeypatch):
    """長者帳號不能改自己的健康註記。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)

    # 長者身分靠 token 的 elder_id claim 認定，這裡不能沿用 _health_note_event
    # 的 elder_id（那個是路徑上的目標長者）
    event = _make_event(
        "POST",
        body={"text": "膝關節退化"},
        path_params={"elder_id": "eld_001"},
        sub="usr_e1",
        elder_id="eld_001",
    )
    event["path"] = "/elders/eld_001/health_notes"
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 403


def test_delete_health_note_success(monkeypatch):
    """依 note_id 刪除單筆，回 200 與刪除後的長者資料。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(
        db,
        "remove_health_note",
        lambda eid, nid: {
            "elder_id": eid,
            "name": "陳阿蘭",
            "health_notes": [],
            "created_at": "2026-07-28T12:00:00+08:00",
        },
    )

    event = _health_note_event("DELETE", note_id="hn_a", sub="usr_c1")
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["health_notes"] == []


def test_delete_health_note_not_found_404(monkeypatch):
    """找不到那一筆時回 404，而不是把 None 當成成功。"""
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(db, "remove_health_note", lambda eid, nid: None)

    event = _health_note_event("DELETE", note_id="hn_missing", sub="usr_c1")
    resp = elders.handler(event, None)

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "HEALTH_NOTE_NOT_FOUND"


# -----------------------------------------------------------------------------
# GET /me
# -----------------------------------------------------------------------------


def _me_event(sub="usr_c1", email="test@example.com"):
    return {
        "httpMethod": "GET",
        "resource": "/me",
        "path": "/v1/me",
        "pathParameters": None,
        "body": None,
        "requestContext": {"authorizer": {"claims": {"sub": sub, "email": email}}},
    }


def test_get_me_caregiver(monkeypatch):
    """照護者可取得 cg_ 短 ID。"""
    monkeypatch.setattr(db, "get_caregiver_by_sub", lambda sub: None)
    monkeypatch.setattr(db, "put_caregiver_lookup", lambda sid, sub, name: None)

    resp = elders.handler(_me_event(sub="abc-123", email="alice@mail.com"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["caregiver_id"].startswith("cg_")
    assert len(body["caregiver_id"]) == 11  # cg_ + 8 hex
    assert body["name"] == "alice"


def test_get_me_elder_forbidden(monkeypatch):
    """長者呼叫 GET /me 回 403。"""
    event = {
        "httpMethod": "GET",
        "resource": "/me",
        "path": "/v1/me",
        "pathParameters": None,
        "body": None,
        "requestContext": {"authorizer": {"claims": {"sub": "usr_e1", "elder_id": "eld_001"}}},
    }
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 403


# -----------------------------------------------------------------------------
# POST /elders/{elder_id}/caregivers
# -----------------------------------------------------------------------------


def test_post_caregiver_link_success(monkeypatch):
    """長者成功綁定照護者。"""
    monkeypatch.setattr(db, "get_caregiver_by_short_id", lambda sid: {"short_id": sid, "sub": "cg-sub-1", "name": "志明"})
    monkeypatch.setattr(db, "get_elder", lambda eid: {"elder_id": "eld_001", "caregiver_ids": []})
    monkeypatch.setattr(db, "link_caregiver_to_elder", lambda eid, sub: {"elder_id": eid, "caregiver_ids": [sub]})

    event = {
        "httpMethod": "POST",
        "resource": "/elders/{elder_id}/caregivers",
        "path": "/v1/elders/eld_001/caregivers",
        "pathParameters": {"elder_id": "eld_001"},
        "body": json.dumps({"caregiver_id": "cg_7f3a91c2"}),
        "requestContext": {"authorizer": {"claims": {"sub": "usr_e1", "elder_id": "eld_001"}}},
    }
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["caregiver_id"] == "cg_7f3a91c2"
    assert body["name"] == "志明"


def test_post_caregiver_link_already_linked(monkeypatch):
    """已綁定的照護者回 200。"""
    monkeypatch.setattr(db, "get_caregiver_by_short_id", lambda sid: {"short_id": sid, "sub": "cg-sub-1", "name": "志明"})
    monkeypatch.setattr(db, "get_elder", lambda eid: {"elder_id": "eld_001", "caregiver_ids": ["cg-sub-1"], "updated_at": "2026-07-14T09:00:00+08:00"})

    event = {
        "httpMethod": "POST",
        "resource": "/elders/{elder_id}/caregivers",
        "path": "/v1/elders/eld_001/caregivers",
        "pathParameters": {"elder_id": "eld_001"},
        "body": json.dumps({"caregiver_id": "cg_7f3a91c2"}),
        "requestContext": {"authorizer": {"claims": {"sub": "usr_e1", "elder_id": "eld_001"}}},
    }
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 200


def test_post_caregiver_link_not_found(monkeypatch):
    """cg_ ID 不存在回 404。"""
    monkeypatch.setattr(db, "get_caregiver_by_short_id", lambda sid: None)
    monkeypatch.setattr(db, "get_elder", lambda eid: {"elder_id": "eld_001", "caregiver_ids": []})

    event = {
        "httpMethod": "POST",
        "resource": "/elders/{elder_id}/caregivers",
        "path": "/v1/elders/eld_001/caregivers",
        "pathParameters": {"elder_id": "eld_001"},
        "body": json.dumps({"caregiver_id": "cg_notexist"}),
        "requestContext": {"authorizer": {"claims": {"sub": "usr_e1", "elder_id": "eld_001"}}},
    }
    resp = elders.handler(event, None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "CAREGIVER_NOT_FOUND"


# -----------------------------------------------------------------------------
# GET /elders/{elder_id}/caregivers
# -----------------------------------------------------------------------------


def _caregivers_event(claims_sub):
    return {
        "httpMethod": "GET",
        "resource": "/elders/{elder_id}/caregivers",
        "path": "/v1/elders/eld_001/caregivers",
        "pathParameters": {"elder_id": "eld_001"},
        "body": None,
        "requestContext": {"authorizer": {"claims": {"sub": claims_sub}}},
    }


def _allow_access(monkeypatch, caller_sub, role=auth.ROLE_CAREGIVER):
    """放行授權，並回傳一個真的 Caller——handler 要用它的 user_id 判斷 is_self。"""
    caller = auth.Caller(role=role, user_id=caller_sub, elder_id=None)
    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: caller)


def test_get_caregivers_list(monkeypatch):
    """列出已綁定照護者。"""
    _allow_access(monkeypatch, "sub-other")
    monkeypatch.setattr(db, "get_elder", lambda eid: {"elder_id": "eld_001", "caregiver_ids": ["sub-1"]})
    monkeypatch.setattr(db, "batch_get_caregivers_by_subs", lambda subs: {
        "sub-1": {"short_id": "cg_aabbccdd", "name": "志明", "created_at": "2026-07-14T09:00:00+08:00"}
    })

    resp = elders.handler(_caregivers_event("sub-other"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["items"]) == 1
    assert body["items"][0]["caregiver_id"] == "cg_aabbccdd"
    assert body["items"][0]["is_self"] is False


def test_get_caregivers_marks_self(monkeypatch):
    """自我註冊的長輩會在自己的清單裡看到自己，那一筆要標 is_self。

    成因：POST /elders 把建立者的 sub 寫進 caregiver_ids，而自我註冊時那個建立者
    就是長輩本人（當下還沒有 elder_id claim）。他沒有 caregiver lookup 記錄
    （那是 GET /me 才寫的），所以 name 是空字串——不標的話長輩畫面上會出現
    一張沒有名字的家人卡。
    """
    _allow_access(monkeypatch, "sub-elder", role=auth.ROLE_ELDER)
    monkeypatch.setattr(
        db, "get_elder",
        lambda eid: {"elder_id": "eld_001", "caregiver_ids": ["sub-elder", "sub-1"]},
    )
    # 自己查不到 lookup；真的被綁進來的家人一定查得到（綁定端點會先驗 lookup 存在）
    monkeypatch.setattr(db, "batch_get_caregivers_by_subs", lambda subs: {
        "sub-1": {"short_id": "cg_aabbccdd", "name": "志明", "created_at": "2026-07-14T09:00:00+08:00"}
    })

    resp = elders.handler(_caregivers_event("sub-elder"), None)
    assert resp["statusCode"] == 200
    items = json.loads(resp["body"])["items"]

    me, family = items[0], items[1]
    assert me["is_self"] is True
    assert me["name"] == ""
    assert me["caregiver_id"] == auth.caregiver_short_id("sub-elder")
    # 不從清單裡拿掉：caregiver_ids 是授權用的真實資料，少回一筆會讓畫面與實際綁定對不起來
    assert len(items) == 2
    assert family["is_self"] is False
    assert family["name"] == "志明"

