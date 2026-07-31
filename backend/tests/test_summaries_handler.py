"""每日摘要 API handler 測試。

除正常路徑，鎖住兩條契約：內部欄位（`input_through_at`、`completeness_rank`、
`generator_version`、`schema_version`）不外流，以及 `sections` 七個 key 每次完整回傳。
"""

import json
from unittest.mock import patch

import pytest

from src.handlers import summaries as summaries_handler
from src.shared.models import SUMMARY_SECTION_KEYS

ELDER = "eld_a1b2c3d4e5f6"
DATE = "2026-07-26"

STORED_SUMMARY = {
    "elder_id": ELDER,
    "date": DATE,
    "overview": "三餐正常並按時服藥",
    "sections": {
        "diet": "三餐正常",
        "activity": None,
        "sleep": None,
        "medication": "血壓藥已按時服用",
        "wellbeing": "提到膝蓋疼痛",
        "safety": None,
        "other": None,
    },
    "routines": {
        "completed": 1,
        "missed": 1,
        "items": [
            {"routine_id": "rtn_001", "title": "吃血壓藥", "status": "done"},
            {"routine_id": "rtn_002", "title": "量血壓", "status": "missed"},
        ],
    },
    "alerts": ["今日多次提到膝蓋疼痛"],
    "interaction_count": 6,
    "data_status": "partial",
    "pending_session_count": 1,
    "generated_at": "2026-07-26T20:00:12.000+08:00",
    # 以下皆為後端內部欄位
    "input_through_at": "2026-07-26T20:00:00.000+08:00",
    "completeness_rank": 0,
    "generator_version": "summary-generator-1",
    "schema_version": 1,
}


def get_event(**params):
    return {
        "httpMethod": "GET",
        "queryStringParameters": {"elder_id": ELDER, **params},
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }


def post_event(body):
    return {
        "httpMethod": "POST",
        "body": json.dumps(body) if isinstance(body, (dict, list)) else body,
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }


@pytest.fixture
def allow_access():
    with patch.object(summaries_handler.auth, "assert_can_access_elder") as mock:
        yield mock


def body_of(response):
    assert response["headers"]["Content-Type"].startswith("application/json")
    return json.loads(response["body"])


# -- GET /summaries -----------------------------------------------------------


def test_list_returns_public_fields_only(allow_access):
    with patch.object(
        summaries_handler.db, "list_daily_summaries", return_value=([STORED_SUMMARY], None)
    ):
        response = summaries_handler.handler(get_event(**{"from": DATE, "to": DATE}), None)

    assert response["statusCode"] == 200
    body = body_of(response)
    assert body["items"][0]["overview"] == "三餐正常並按時服藥"
    assert body["items"][0]["data_status"] == "partial"
    assert body["items"][0]["pending_session_count"] == 1
    assert "next_token" not in body

    raw = response["body"]
    for internal in ("input_through_at", "completeness_rank", "generator_version", "schema_version"):
        assert internal not in raw


def test_list_always_returns_seven_sections(allow_access):
    sparse = {**STORED_SUMMARY, "sections": {"diet": "三餐正常"}}
    with patch.object(summaries_handler.db, "list_daily_summaries", return_value=([sparse], None)):
        response = summaries_handler.handler(get_event(), None)
    sections = body_of(response)["items"][0]["sections"]
    assert set(sections) == set(SUMMARY_SECTION_KEYS)
    assert sections["diet"] == "三餐正常"
    assert sections["sleep"] is None


def test_list_defaults_to_last_seven_days(allow_access):
    with patch.object(
        summaries_handler.db, "list_daily_summaries", return_value=([], None)
    ) as mock_list:
        summaries_handler.handler(get_event(to=DATE), None)
    kwargs = mock_list.call_args.kwargs
    assert kwargs["from_date"] == "2026-07-20"
    assert kwargs["to_date"] == DATE
    assert kwargs["limit"] == summaries_handler.DEFAULT_LIMIT


def test_list_returns_next_token_at_top_level(allow_access):
    with patch.object(
        summaries_handler.db, "list_daily_summaries", return_value=([STORED_SUMMARY], "eyJhIjoxfQ==")
    ):
        response = summaries_handler.handler(get_event(), None)
    assert body_of(response)["next_token"] == "eyJhIjoxfQ=="


@pytest.mark.parametrize(
    "params",
    [
        {"elder_id": ""},
        {"from": "2026-7-1"},
        {"from": "2026-07-27", "to": "2026-07-26"},
        {"limit": "0"},
        {"limit": "101"},
        {"limit": "abc"},
    ],
)
def test_list_rejects_bad_parameters(allow_access, params):
    event = get_event(**params)
    if params.get("elder_id") == "":
        event["queryStringParameters"]["elder_id"] = ""
    with patch.object(summaries_handler.db, "list_daily_summaries", return_value=([], None)):
        response = summaries_handler.handler(event, None)
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


def test_list_maps_broken_token_to_400(allow_access):
    error = summaries_handler.db.DBError("無效的分頁游標 next_token: boom")
    with patch.object(summaries_handler.db, "list_daily_summaries", side_effect=error):
        response = summaries_handler.handler(get_event(next_token="broken"), None)
    assert response["statusCode"] == 400


def test_list_maps_other_db_errors_to_500(allow_access):
    error = summaries_handler.db.DBError("查詢每日摘要失敗: boom")
    with patch.object(summaries_handler.db, "list_daily_summaries", side_effect=error):
        response = summaries_handler.handler(get_event(), None)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "INTERNAL_ERROR"


def test_list_propagates_authorization_failure():
    denied = summaries_handler.responses.error(403, "FORBIDDEN", "無權存取此長者的資料")
    with patch.object(
        summaries_handler.auth,
        "assert_can_access_elder",
        side_effect=summaries_handler.auth.AuthError(denied),
    ):
        response = summaries_handler.handler(get_event(), None)
    assert response["statusCode"] == 403


# -- POST /summaries/generate -------------------------------------------------


def test_generate_returns_single_summary(allow_access):
    with patch.object(
        summaries_handler.summarizer, "generate_and_store", return_value=(STORED_SUMMARY, True)
    ) as mock_generate:
        response = summaries_handler.handler(post_event({"elder_id": ELDER, "date": DATE}), None)

    assert response["statusCode"] == 200
    body = body_of(response)
    assert body["date"] == DATE
    assert "items" not in body
    assert mock_generate.call_args.args == (ELDER, DATE)
    for internal in ("input_through_at", "completeness_rank", "generator_version"):
        assert internal not in response["body"]


def test_generate_defaults_to_today(allow_access):
    with patch.object(
        summaries_handler.summarizer, "generate_and_store", return_value=(STORED_SUMMARY, True)
    ) as mock_generate:
        summaries_handler.handler(post_event({"elder_id": ELDER}), None)
    date_arg = mock_generate.call_args.args[1]
    assert len(date_arg) == len("2026-07-26")


def test_generate_returns_existing_when_overwrite_rejected(allow_access):
    existing = {**STORED_SUMMARY, "data_status": "complete", "overview": "既有完整版本"}
    with patch.object(
        summaries_handler.summarizer, "generate_and_store", return_value=(existing, False)
    ):
        response = summaries_handler.handler(post_event({"elder_id": ELDER, "date": DATE}), None)
    # 覆寫被優先序擋下對呼叫端仍是成功，回傳目前生效的那份
    assert response["statusCode"] == 200
    assert body_of(response)["overview"] == "既有完整版本"


@pytest.mark.parametrize(
    "body",
    [
        {"elder_id": ""},
        {"date": DATE},
        {"elder_id": ELDER, "date": "26-07-2026"},
        "not-json",
        "[]",
    ],
)
def test_generate_rejects_bad_body(allow_access, body):
    with patch.object(summaries_handler.summarizer, "generate_and_store") as mock_generate:
        response = summaries_handler.handler(post_event(body), None)
    assert response["statusCode"] == 400
    assert mock_generate.call_count == 0


def test_generate_maps_model_failure_to_500(allow_access):
    with patch.object(
        summaries_handler.summarizer,
        "generate_and_store",
        side_effect=summaries_handler.bedrock.BedrockError("boom"),
    ):
        response = summaries_handler.handler(post_event({"elder_id": ELDER}), None)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "INTERNAL_ERROR"


def test_unsupported_method_is_rejected(allow_access):
    response = summaries_handler.handler({"httpMethod": "DELETE"}, None)
    assert response["statusCode"] == 400
