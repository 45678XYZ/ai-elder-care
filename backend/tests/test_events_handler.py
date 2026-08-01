"""GET /events handler 測試。

除了正常路徑，特別鎖住「不外流 extraction internals」這條契約：canonical key、
extraction track、revision、chunk、structured_detail、evidence 清單
都不得出現在回應裡（docs/api.md）。
"""

import json
from unittest.mock import patch

import pytest

from src.handlers import events as events_handler

ELDER = "eld_a1b2c3d4e5f6"

STORED_EVENT = {
    "event_id": "evt_be08a35d6af5",
    "elder_id": ELDER,
    "ts": "2026-07-26T09:05:00.000+08:00",
    "event_time_key": "2026-07-26T09:05:00.000+08:00#evt_be08a35d6af5",
    "type": "medication",
    "detail": "早餐後服用血壓藥一顆",
    "source": "conversation",
    "conversation_id": "cnv_1",
    "routine_id": "rtn_001",
    # 以下皆為後端內部欄位
    "canonical_event_key": "2026-07-26#SLOT_0900#長者#服用血壓藥",
    "extraction_track": "batch",
    "taxonomy_version": "uco-1.0.0",
    "structured_detail": {"medication_item": "血壓藥", "pill_count": 1},
    "evidence_conversation_ids": ["cnv_1", "cnv_2"],
    "source_chunk_id": "chk_1",
    "revision": 1,
    "schema_version": 1,
    "confidence": 0.9,
}


def make_event(**params):
    return {
        "httpMethod": "GET",
        "queryStringParameters": {"elder_id": ELDER, **params},
        "requestContext": {"authorizer": {"claims": {"sub": "caregiver-sub"}}},
    }


@pytest.fixture
def allow_access():
    with patch.object(events_handler.auth, "assert_can_access_elder") as mock:
        yield mock


def body_of(response):
    assert response["headers"]["Content-Type"].startswith("application/json")
    return json.loads(response["body"])


def test_returns_public_fields_only(allow_access):
    with patch.object(events_handler.db, "list_events", return_value=([STORED_EVENT], None)):
        response = events_handler.handler(make_event(**{"from": "2026-07-26", "to": "2026-07-26"}), None)

    assert response["statusCode"] == 200
    body = body_of(response)
    assert body["items"] == [
        {
            "event_id": "evt_be08a35d6af5",
            "elder_id": ELDER,
            "ts": "2026-07-26T09:05:00.000+08:00",
            "type": "medication",
            "detail": "早餐後服用血壓藥一顆",
            "source": "conversation",
            "conversation_id": "cnv_1",
            "routine_id": "rtn_001",
        }
    ]
    assert "next_token" not in body

    raw = response["body"]
    for internal in (
        "canonical_event_key",
        "extraction_track",
        "taxonomy_version",
        "structured_detail",
        "evidence_conversation_ids",
        "source_chunk_id",
        "revision",
        "event_time_key",
    ):
        assert internal not in raw


def test_optional_fields_are_omitted_not_null(allow_access):
    minimal = {k: v for k, v in STORED_EVENT.items() if k not in ("conversation_id", "routine_id")}
    with patch.object(events_handler.db, "list_events", return_value=([minimal], None)):
        response = events_handler.handler(make_event(), None)
    item = body_of(response)["items"][0]
    assert "conversation_id" not in item
    assert "routine_id" not in item


def test_next_token_is_returned_at_top_level(allow_access):
    with patch.object(events_handler.db, "list_events", return_value=([STORED_EVENT], "eyJhIjoxfQ==")):
        response = events_handler.handler(make_event(), None)
    assert body_of(response)["next_token"] == "eyJhIjoxfQ=="


def test_defaults_to_today_in_taipei(allow_access):
    with patch.object(events_handler.db, "list_events", return_value=([], None)) as mock_list:
        events_handler.handler(make_event(), None)
    kwargs = mock_list.call_args.kwargs
    assert kwargs["from_date"] == kwargs["to_date"]
    assert len(kwargs["from_date"]) == len("2026-07-26")
    assert kwargs["limit"] == events_handler.DEFAULT_LIMIT


def test_passes_through_filters(allow_access):
    with patch.object(events_handler.db, "list_events", return_value=([], None)) as mock_list:
        events_handler.handler(
            make_event(**{"from": "2026-07-01", "to": "2026-07-26", "type": "safety", "limit": "10"}),
            None,
        )
    kwargs = mock_list.call_args.kwargs
    assert kwargs["from_date"] == "2026-07-01"
    assert kwargs["to_date"] == "2026-07-26"
    assert kwargs["event_type"] == "safety"
    assert kwargs["limit"] == 10


def test_missing_elder_id():
    response = events_handler.handler(
        {"queryStringParameters": {}, "requestContext": {"authorizer": {"claims": {"sub": "x"}}}},
        None,
    )
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026/07/26"},
        {"to": "26-07-2026"},
        {"from": "2026-07-27", "to": "2026-07-26"},
        {"type": "fall"},
        {"limit": "0"},
        {"limit": "500"},
        {"limit": "abc"},
    ],
)
def test_invalid_parameters(allow_access, params):
    with patch.object(events_handler.db, "list_events", return_value=([], None)):
        response = events_handler.handler(make_event(**params), None)
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


def test_forbidden_is_propagated_from_auth():
    from src.shared import responses

    forbidden = responses.error(403, "FORBIDDEN", "無權存取此長者的資料")
    with patch.object(
        events_handler.auth,
        "assert_can_access_elder",
        side_effect=events_handler.auth.AuthError(forbidden),
    ):
        response = events_handler.handler(make_event(), None)
    assert response["statusCode"] == 403
    assert body_of(response)["error"]["code"] == "FORBIDDEN"


def test_invalid_next_token_is_client_error(allow_access):
    from src.shared import db as db_module

    with patch.object(
        events_handler.db,
        "list_events",
        side_effect=db_module.DBError("無效的分頁游標 next_token: bad"),
    ):
        response = events_handler.handler(make_event(next_token="x"), None)
    assert response["statusCode"] == 400
    assert body_of(response)["error"]["code"] == "INVALID_PARAMETER"


def test_db_failure_is_internal_error(allow_access):
    from src.shared import db as db_module

    with patch.object(
        events_handler.db, "list_events", side_effect=db_module.DBError("查詢生活事件失敗: boom")
    ):
        response = events_handler.handler(make_event(), None)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "INTERNAL_ERROR"
