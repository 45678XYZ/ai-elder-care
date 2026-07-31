"""src.handlers.chat 測試：對話核心 API、輸入邊界與 turn 冪等流程（moto）。

對應 docs/api.md 的 `POST /chat` routing 規則：冪等判定先於 session 選擇與 reserve，
completed／failed 重播原結果，processing 租約未到期回 409。
"""

import base64
import importlib
import json

import boto3
import pytest
from moto import mock_aws

CONVERSATIONS_TABLE = "conversations-test"
ELDER = "eld_a1b2c3d4e5f6"
REQUEST_ID = "ad381d1e-2b96-4dc2-83ac-c58ab0b934db"
TRANSCRIPT = "小助手，我今天已經吃過血壓藥了"
REPLY = "阿蘭嬤，太棒了！我已經幫您登記好血壓藥囉。"


class DummyTTS:
    def synthesize(self, text):
        return b"mock-mp3-bytes"


def _make_event(body_dict, elder_id=ELDER):
    """輔助函式：建立帶有 Cognito 授權的 API Gateway Event。"""
    return {
        "body": json.dumps(body_dict),
        "requestContext": {
            "authorizer": {"claims": {"sub": "usr_elder", "elder_id": elder_id}}
        },
    }


def chat_body(**overrides):
    body = {
        "client_request_id": REQUEST_ID,
        "elder_id": ELDER,
        "lang": "zh-TW",
        "text": TRANSCRIPT,
    }
    body.update(overrides)
    return body


@pytest.fixture
def stack(monkeypatch):
    """在 moto 環境下重載整條寫入路徑，讓 turn 狀態機真的寫進 DynamoDB。"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", CONVERSATIONS_TABLE)

    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName=CONVERSATIONS_TABLE,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "record_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "record_id", "AttributeType": "S"},
                {"AttributeName": "conversation_time_key", "AttributeType": "S"},
                {"AttributeName": "session_state_key", "AttributeType": "S"},
                {"AttributeName": "session_state_time_key", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "conversations-by-time",
                    "KeySchema": [
                        {"AttributeName": "elder_id", "KeyType": "HASH"},
                        {"AttributeName": "conversation_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "sessions-by-state",
                    "KeySchema": [
                        {"AttributeName": "session_state_key", "KeyType": "HASH"},
                        {"AttributeName": "session_state_time_key", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db = importlib.reload(importlib.import_module("src.shared.db"))
        sessions = importlib.reload(importlib.import_module("src.shared.sessions"))
        turns = importlib.reload(importlib.import_module("src.shared.turns"))
        chat = importlib.reload(importlib.import_module("src.handlers.chat"))

        calls = []
        monkeypatch.setattr(
            chat, "invoke_agent_brain", lambda eid, txt: calls.append(txt) or (REPLY, True)
        )
        monkeypatch.setattr(chat.TTSFactory, "get_tts_engine", lambda lang: DummyTTS())
        monkeypatch.setattr(
            chat, "upload_audio_to_s3", lambda audio, conv_id: f"tts/{conv_id}.mp3"
        )
        monkeypatch.setattr(
            chat, "presign_audio", lambda key: f"https://s3.example.com/{key}" if key else None
        )

        yield chat, db, sessions, turns, calls

    for name in (
        "src.shared.db",
        "src.shared.sessions",
        "src.shared.turns",
        "src.handlers.chat",
    ):
        importlib.reload(importlib.import_module(name))


def post(chat, **overrides):
    return chat.handler(_make_event(chat_body(**overrides)), None)


def body_of(response):
    return json.loads(response["body"])


# -- 輸入校驗（不需要資料庫）----------------------------------------------------


def test_chat_invalid_json():
    from src.handlers import chat

    resp = chat.handler({"body": "invalid-json-string"}, None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_JSON"


def test_chat_missing_elder_id():
    from src.handlers import chat

    event = {
        "body": json.dumps({"client_request_id": REQUEST_ID, "lang": "zh-TW", "text": "你好"}),
        "requestContext": {"authorizer": {"claims": {"sub": "usr_elder"}}},
    }
    resp = chat.handler(event, None)


    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_missing_client_request_id():
    """沒有冪等鍵就沒有冪等性，不能放行。"""
    from src.handlers import chat

    resp = chat.handler(_make_event({"elder_id": ELDER, "lang": "zh-TW", "text": "你好"}), None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_empty_client_request_id():
    """空字串會讓同一長者的每次請求都塌到同一個 turn。"""
    from src.handlers import chat

    resp = chat.handler(_make_event(chat_body(client_request_id="")), None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_invalid_lang():
    from src.handlers import chat

    resp = chat.handler(_make_event(chat_body(lang="en-US")), None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_requires_text_or_audio():
    from src.handlers import chat

    body = chat_body()
    body.pop("text")
    resp = chat.handler(_make_event(body), None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_rejects_text_and_audio_together():
    from src.handlers import chat

    resp = chat.handler(
        _make_event(chat_body(audio={"data": "dGVzdA==", "format": "m4a"})), None
    )
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


def test_chat_audio_too_long():
    """超長音檔在 reserve 之前就擋下，不佔用 session 名額。"""
    from src.handlers import chat

    oversized = base64.b64encode(b"0" * (5 * 1024 * 1024 + 100)).decode("utf-8")
    body = chat_body(audio={"data": oversized, "format": "m4a"})
    body.pop("text")

    resp = chat.handler(_make_event(body), None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "AUDIO_TOO_LONG"


def test_chat_forbidden_for_another_elder():
    from src.handlers import chat

    resp = chat.handler(_make_event(chat_body(), elder_id="eld_ffffffffffff"), None)
    assert resp["statusCode"] == 403


# -- 正常流程 ------------------------------------------------------------------


def test_first_turn_creates_a_session_and_completes(stack):
    chat, _, sessions, turns, _ = stack
    response = post(chat)
    assert response["statusCode"] == 200
    body = body_of(response)

    assert body["transcript"] == TRANSCRIPT
    assert body["reply_text"] == REPLY
    assert body["routines_updated"] is True
    assert body["conversation_id"].startswith("cnv_")
    assert body["session_id"].startswith("ses_")
    assert body["reply_audio_url"].endswith(f"tts/{body['conversation_id']}.mp3")

    turn = turns.get_turn(ELDER, body["conversation_id"])
    assert turn["request_status"] == turns.STATUS_COMPLETED
    assert turn["elder_transcript"] == TRANSCRIPT
    assert turn["ai_respond_text"] == REPLY
    # 音訊只存 object key，URL 每次重新簽發
    assert turn["ai_respond_audio_s3_key"] == f"tts/{body['conversation_id']}.mp3"
    assert "ai_respond_audio_url" not in turn

    session = sessions.get_session(ELDER, body["session_id"])
    assert session["turn_ids"] == [body["conversation_id"]]
    assert session["inflight_turn_count"] == 0
    assert session["state"] == sessions.STATE_ACTIVE



def test_completed_turn_is_countable_as_an_interaction(stack):
    """/chat 寫下的 turn 必須被統計讀到（GET /stats 的 interaction_count）。"""
    chat, db, _, turns, _ = stack
    body = body_of(post(chat))
    created_at = turns.get_turn(ELDER, body["conversation_id"])["created_at"]
    date = created_at[:10]

    assert db.list_turn_times(ELDER, date, date) == [created_at]


def test_second_turn_reuses_the_session(stack):
    chat, _, sessions, _, _ = stack
    first = body_of(post(chat))
    second = body_of(
        post(chat, client_request_id="second-request", session_id=first["session_id"])
    )

    assert second["session_id"] == first["session_id"]
    assert second["conversation_id"] != first["conversation_id"]
    session = sessions.get_session(ELDER, first["session_id"])
    assert session["turn_ids"] == [first["conversation_id"], second["conversation_id"]]


def test_unknown_session_returns_404(stack):
    chat, _, _, _, _ = stack
    response = post(chat, session_id="ses_nope")
    assert response["statusCode"] == 404
    assert body_of(response)["error"]["code"] == "SESSION_NOT_FOUND"


def test_closed_session_is_replaced_by_a_new_one(stack):
    """原 session 已收斂時不得追加，必須改用新的 active session。"""
    chat, _, sessions, _, _ = stack
    first = body_of(post(chat))
    sessions.begin_closing(ELDER, first["session_id"], close_reason="client_requested")

    second = body_of(
        post(chat, client_request_id="second-request", session_id=first["session_id"])
    )
    assert second["session_id"] != first["session_id"]
    assert sessions.get_session(ELDER, second["session_id"])["state"] == sessions.STATE_ACTIVE


# -- 冪等 ----------------------------------------------------------------------


def test_resend_replays_the_result_without_reprocessing(stack):
    chat, _, sessions, _, calls = stack
    first = body_of(post(chat))
    second = body_of(post(chat))

    assert second["conversation_id"] == first["conversation_id"]
    assert second["session_id"] == first["session_id"]
    assert second["reply_text"] == first["reply_text"]
    # 重送不得再叫一次大腦，也不得追加第二輪
    assert calls == [TRANSCRIPT]
    assert sessions.get_session(ELDER, first["session_id"])["turn_count"] == 1


def test_same_request_id_with_different_content_conflicts(stack):
    chat, _, _, _, _ = stack
    post(chat)
    response = post(chat, text="我今天沒有吃藥")
    assert response["statusCode"] == 409
    assert body_of(response)["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_resend_with_a_new_session_id_is_still_a_replay(stack):
    """session_id 是 routing 提示不是請求內容，換了不算換內容。"""
    chat, _, _, _, calls = stack
    first = body_of(post(chat))
    second = body_of(post(chat, session_id=first["session_id"]))

    assert second["conversation_id"] == first["conversation_id"]
    assert calls == [TRANSCRIPT]


def test_in_flight_request_returns_409(stack):
    chat, _, sessions, turns, _ = stack
    session = sessions.create_session(ELDER)
    conversation_id = turns.conversation_id_for(ELDER, REQUEST_ID)
    turns.reserve(
        ELDER,
        session["session_id"],
        turn={
            "conversation_id": conversation_id,
            "client_request_id": REQUEST_ID,

            "request_hash": chat.request_hash(
                chat.ChatRequest.model_validate(chat_body()), b""
            ),
            "lang": "zh-TW",
            "input_type": "text",
        },
        owner="another-invocation",
    )

    response = post(chat)
    assert response["statusCode"] == 409
    assert body_of(response)["error"]["code"] == "REQUEST_IN_PROGRESS"


def test_expired_lease_is_taken_over_and_completed(stack, monkeypatch):
    """前一個 invocation 死在半路：租約到期後由重送接管，仍是同一輪對話。"""
    chat, _, sessions, turns, _ = stack
    session = sessions.create_session(ELDER)
    conversation_id = turns.conversation_id_for(ELDER, REQUEST_ID)
    monkeypatch.setattr(turns, "REQUEST_LEASE_SECONDS", -1)
    turns.reserve(
        ELDER,
        session["session_id"],
        turn={
            "conversation_id": conversation_id,
            "client_request_id": REQUEST_ID,

            "request_hash": chat.request_hash(
                chat.ChatRequest.model_validate(chat_body()), b""
            ),
            "lang": "zh-TW",
            "input_type": "text",
        },
        owner="dead-invocation",
    )

    body = body_of(post(chat))
    assert body["conversation_id"] == conversation_id
    assert body["session_id"] == session["session_id"]
    stored = sessions.get_session(ELDER, session["session_id"])
    assert stored["turn_ids"] == [conversation_id]
    assert stored["inflight_turn_count"] == 0


# -- 失敗路徑 ------------------------------------------------------------------


def test_tts_failure_is_terminal_and_frees_the_session(stack, monkeypatch):
    chat, db, sessions, turns, _ = stack

    def broken(lang):
        raise RuntimeError("polly down")

    monkeypatch.setattr(chat.TTSFactory, "get_tts_engine", broken)
    response = post(chat)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "TTS_FAILED"

    turn = turns.get_turn(ELDER, turns.conversation_id_for(ELDER, REQUEST_ID))
    assert turn["request_status"] == turns.STATUS_FAILED
    assert turn["error_code"] == "TTS_FAILED"
    session = sessions.get_session(ELDER, turn["session_id"])
    # 名額必須還回去，否則 session 永遠關不起來
    assert session["inflight_turn_count"] == 0
    assert session["turn_ids"] == []
    date = turn["created_at"][:10]
    assert db.list_turn_times(ELDER, date, date) == []


def test_failed_turn_replays_the_same_error(stack, monkeypatch):
    """failed 是終態：重試必須換新的 client_request_id，不是重跑同一個。"""
    chat, _, _, _, calls = stack

    def broken(lang):
        raise RuntimeError("polly down")

    monkeypatch.setattr(chat.TTSFactory, "get_tts_engine", broken)
    post(chat)
    monkeypatch.setattr(chat.TTSFactory, "get_tts_engine", lambda lang: DummyTTS())

    response = post(chat)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "TTS_FAILED"
    assert calls == [TRANSCRIPT]


def test_unstored_audio_does_not_become_a_dead_link(stack, monkeypatch):
    """存不進 S3 就不留 key：帶著 key 提交成 completed，之後每次重播都是一條死連結。"""
    chat, _, _, turns, _ = stack
    monkeypatch.setattr(chat, "upload_audio_to_s3", lambda audio, conv_id: None)

    body = body_of(post(chat))
    assert body["reply_audio_url"] is None
    # 回覆內容與副作用都是真的，這一輪仍然成立
    assert body["reply_text"] == REPLY

    turn = turns.get_turn(ELDER, body["conversation_id"])
    assert turn["request_status"] == turns.STATUS_COMPLETED
    assert "ai_respond_audio_s3_key" not in turn


def test_bedrock_failure_is_recorded_as_failed(stack, monkeypatch):
    chat, _, _, turns, _ = stack

    def broken(elder_id, transcript):
        raise RuntimeError("bedrock down")

    monkeypatch.setattr(chat, "invoke_agent_brain", broken)
    response = post(chat)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "BEDROCK_ERROR"

    turn = turns.get_turn(ELDER, turns.conversation_id_for(ELDER, REQUEST_ID))
    assert turn["request_status"] == turns.STATUS_FAILED
    # 保存的錯誤要是穩定且安全化的，不得夾帶內部例外訊息
    assert "bedrock down" not in turn["error_message"]
