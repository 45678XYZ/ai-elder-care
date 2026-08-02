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


class DummyTTSFacade:
    """合成已移出同步路徑，chat 只問「這輪會不會有音訊」。"""

    def is_available(self, language, dialect):
        return True


class DummySqsClient:
    """記錄 chat 送出的合成工作，供測試檢查入列內容。"""

    def __init__(self):
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return {"MessageId": "msg-1"}


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
            chat,
            "invoke_agent_brain",
            lambda eid, txt, lang="zh-TW", **kw: calls.append(txt) or (REPLY, True),
        )
        monkeypatch.setattr(chat.db, "get_elder", lambda elder_id: {"elder_id": elder_id})
        monkeypatch.setattr(chat, "get_tts_facade", lambda: DummyTTSFacade())
        monkeypatch.setattr(chat, "TTS_QUEUE_URL", "https://sqs.example.com/tts")
        sqs = DummySqsClient()
        monkeypatch.setattr(chat, "get_sqs_client", lambda: sqs)
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


def test_agent_receives_explicit_language(monkeypatch):
    """語言由 payload 明確指定，不讓大腦從逐字稿內容自行猜測或切換。"""
    from src.handlers import chat

    class Body:
        @staticmethod
        def read():
            return json.dumps({"reply_text": "食飽咧。"}).encode()

    class Client:
        def invoke_agent_runtime(self, **kwargs):
            self.kwargs = kwargs
            return {"response": Body()}

    client = Client()
    monkeypatch.setattr(chat, "AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:::runtime/x")
    monkeypatch.setattr(chat, "get_agentcore_client", lambda: client)

    reply, _ = chat.invoke_agent_brain(ELDER, "食飽吂？", "hak")

    payload = json.loads(client.kwargs["payload"].decode())
    assert payload["lang"] == "hak"
    assert payload["elder_id"] == ELDER
    assert reply == "食飽咧。"


def test_local_hakka_agent_fallback_does_not_switch_to_chinese(monkeypatch):
    from src.handlers import chat

    monkeypatch.setattr(chat, "AGENTCORE_RUNTIME_ARN", "")
    reply, _ = chat.invoke_agent_brain(ELDER, "食飽吂？", "hak")

    assert "𠊎" in reply


# -- 輸入校驗（不需要資料庫）----------------------------------------------------


def test_chat_invalid_json():
    from src.handlers import chat

    resp = chat.handler({"body": "invalid-json-string"}, None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "INVALID_PARAMETER"


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
    # 合成非同步進行；key 由 worker 在 MP3 真的寫進 S3 之後才補上
    assert "ai_respond_audio_s3_key" not in turn
    assert turn["ai_respond_audio_pending"] is True
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


def test_tts_failure_completes_text_turn_and_frees_the_session(stack, monkeypatch):
    chat, db, sessions, turns, _ = stack

    class NoProviderTTSFacade:
        def is_available(self, language, dialect):
            return False

    monkeypatch.setattr(chat, "get_tts_facade", lambda: NoProviderTTSFacade())
    response = post(chat)
    assert response["statusCode"] == 200
    body = body_of(response)
    # 沒有可用 provider 時不入列，也不讓 App 空等一段不會來的音訊
    assert body["reply_audio_url"] is None
    assert body["reply_audio_status"] == "unavailable"

    turn = turns.get_turn(ELDER, turns.conversation_id_for(ELDER, REQUEST_ID))
    assert turn["request_status"] == turns.STATUS_COMPLETED
    assert turn["ai_respond_text"] == REPLY
    session = sessions.get_session(ELDER, turn["session_id"])
    assert session["inflight_turn_count"] == 0
    assert session["turn_ids"] == [turn["conversation_id"]]
    date = turn["created_at"][:10]
    assert len(db.list_turn_times(ELDER, date, date)) == 1


def test_tts_failure_replays_the_completed_text_result(stack, monkeypatch):
    """TTS 失敗仍是 completed；同一冪等請求不重跑 Agent。"""
    chat, _, _, _, calls = stack

    class BrokenTTSFacade:
        def is_available(self, language, dialect):
            raise RuntimeError("tts config exploded")

    monkeypatch.setattr(chat, "get_tts_facade", lambda: BrokenTTSFacade())
    first = body_of(post(chat))
    # TTS 爆掉不影響文字 turn：內容與副作用都是真的
    assert first["reply_text"] == REPLY
    assert first["reply_audio_status"] == "unavailable"

    monkeypatch.setattr(chat, "get_tts_facade", lambda: DummyTTSFacade())
    response = post(chat)
    assert response["statusCode"] == 200
    # 冪等重送不重跑 Agent，也不會把已終態的 turn 改成 pending
    assert body_of(response)["reply_audio_status"] == "unavailable"
    assert calls == [TRANSCRIPT]


def test_pending_audio_is_not_committed_as_a_key(stack):
    """合成還沒完成就不留 key：帶著 key 提交成 completed，之後每次重播都是一條死連結。"""
    chat, _, _, turns, _ = stack

    body = body_of(post(chat))
    # URL 有簽出來讓 App 輪詢，但狀態明確標示音訊還沒就緒
    assert body["reply_audio_status"] == "pending"
    assert body["reply_audio_url"] is not None
    assert body["reply_text"] == REPLY

    turn = turns.get_turn(ELDER, body["conversation_id"])
    assert turn["request_status"] == turns.STATUS_COMPLETED
    # key 要等 worker 真的把 MP3 寫進 S3 才由它補上
    assert "ai_respond_audio_s3_key" not in turn
    assert turn["ai_respond_audio_pending"] is True


def test_enqueued_synthesis_carries_what_the_worker_needs(stack):
    """入列內容缺一不可：worker 靠它決定寫到哪個 key、補寫哪個 turn。"""
    chat, _, _, _, _ = stack
    body = body_of(post(chat))

    messages = chat.get_sqs_client().messages
    assert len(messages) == 1
    payload = json.loads(messages[0]["MessageBody"])
    assert payload["elder_id"] == ELDER
    assert payload["conversation_id"] == body["conversation_id"]
    assert payload["object_key"] == f"tts/{body['conversation_id']}.mp3"
    assert payload["text"] == REPLY
    assert payload["language"] == "zh-TW"


def test_bedrock_failure_is_recorded_as_failed(stack, monkeypatch):
    chat, _, _, turns, _ = stack

    def broken(elder_id, transcript, lang="zh-TW", **kw):
        raise RuntimeError("bedrock down")

    monkeypatch.setattr(chat, "invoke_agent_brain", broken)
    response = post(chat)
    assert response["statusCode"] == 500
    assert body_of(response)["error"]["code"] == "INTERNAL_ERROR"

    turn = turns.get_turn(ELDER, turns.conversation_id_for(ELDER, REQUEST_ID))
    assert turn["request_status"] == turns.STATUS_FAILED
    # 保存的錯誤要是穩定且安全化的，不得夾帶內部例外訊息
    assert "bedrock down" not in turn["error_message"]


# =============================================================================
# 對話大腦呼叫層（AgentCore Runtime）
# =============================================================================

def _chat_module():
    """取未經 moto 重載的 chat 模組；本節只測純函式與 client 呼叫組裝。"""
    return importlib.import_module("src.handlers.chat")


def test_runtime_session_id_meets_api_minimum_length():
    """AgentCore 規定 runtimeSessionId 最短 33 字元，直接傳 elder_id 會被 API 擋掉。"""
    chat = _chat_module()
    assert len(chat.runtime_session_id("eld_001")) >= 33


def test_runtime_session_id_is_stable_per_elder():
    """同一位長者必須永遠得到同一個值：這個 ID 就是託管記憶的對話串鍵。"""
    chat = _chat_module()
    assert chat.runtime_session_id(ELDER) == chat.runtime_session_id(ELDER)
    assert chat.runtime_session_id(ELDER) != chat.runtime_session_id("eld_other")


class _FakeStreamingBody:
    def __init__(self, payload):
        self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._payload


class _FakeAgentCoreClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": _FakeStreamingBody(self._payload)}


def test_invoke_agent_brain_reads_flags_from_payload(monkeypatch):
    """旗標由 runtime 明確回報，不再靠掃 trace 字串猜工具有沒有被呼叫。"""
    chat = _chat_module()
    fake = _FakeAgentCoreClient(
        {"reply_text": REPLY, "routines_updated": True}
    )
    monkeypatch.setattr(chat, "AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:::runtime/x")
    monkeypatch.setattr(chat, "get_agentcore_client", lambda: fake)

    reply, routines_updated = chat.invoke_agent_brain(ELDER, TRANSCRIPT, "zh-TW")

    assert reply == REPLY
    assert routines_updated is True

    sent = fake.calls[0]
    assert len(sent["runtimeSessionId"]) >= 33
    payload = json.loads(sent["payload"].decode("utf-8"))
    # 時間戳走獨立欄位，不混進長者原話——否則會一起被寫進長期記憶
    assert payload["text"] == TRANSCRIPT
    assert payload["local_time"]
    assert payload["elder_id"] == ELDER


def test_invoke_agent_brain_falls_back_on_empty_reply(monkeypatch):
    """模型沒產出文字時要有保底回覆，長者至少聽得到一句話。"""
    chat = _chat_module()
    fake = _FakeAgentCoreClient({"reply_text": "  ", "routines_updated": False})
    monkeypatch.setattr(chat, "AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:::runtime/x")
    monkeypatch.setattr(chat, "get_agentcore_client", lambda: fake)

    reply, routines_updated = chat.invoke_agent_brain(ELDER, TRANSCRIPT)

    assert reply.strip()
    assert routines_updated is False
