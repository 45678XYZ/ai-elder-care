"""src.handlers.chat 單元測試：對話核心 API 與流程邊界防禦驗證。"""

import base64
import json

import pytest

from src.handlers import chat
from src.shared import auth, db


def _make_event(body_dict, elder_id="eld_001"):
    """輔助函式：建立帶有 Cognito 授權的 API Gateway Event。"""
    return {
        "body": json.dumps(body_dict),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "usr_elder",
                    "elder_id": elder_id
                }
            }
        }
    }


def test_chat_invalid_json():
    """測試非有效 JSON body 回傳 400 INVALID_JSON。"""
    event = {"body": "invalid-json-string"}
    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_JSON"


def test_chat_missing_elder_id():
    """測試缺少 elder_id 回傳 400 INVALID_PARAM。"""
    body = {"lang": "zh-TW", "text": "你好"}
    event = {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "usr_elder"
                }
            }
        }
    }

    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAM"


def test_chat_invalid_lang():
    """測試不符合規範之語言選項回傳 400 INVALID_PARAM。"""
    event = _make_event({"elder_id": "eld_001", "lang": "en-US", "text": "Hello"})
    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAM"


def test_chat_both_text_and_audio_missing():
    """測試 text 與 audio 皆未提供時回傳 400 INVALID_PARAM。"""
    event = _make_event({"elder_id": "eld_001", "lang": "zh-TW"})
    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAM"


def test_chat_both_text_and_audio_provided():
    """測試同時提供 text 與 audio 時回傳 400 INVALID_PARAM。"""
    event = _make_event({
        "elder_id": "eld_001",
        "lang": "zh-TW",
        "text": "你好",
        "audio": {"data": "dGVzdA==", "format": "m4a"}
    })
    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INVALID_PARAM"


def test_chat_audio_too_long(monkeypatch):
    """測試語音超過 5MB (約 60 秒) 時回傳 400 AUDIO_TOO_LONG。"""
    fake_large_bytes = b"0" * (5 * 1024 * 1024 + 100)
    fake_b64 = base64.b64encode(fake_large_bytes).decode("utf-8")

    event = _make_event({
        "elder_id": "eld_001",
        "lang": "zh-TW",
        "audio": {"data": fake_b64, "format": "m4a"}
    })

    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)

    resp = chat.handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "AUDIO_TOO_LONG"


class DummyTTS:
    def synthesize(self, text):
        return b"mock-mp3-bytes"


def test_chat_success_text_path(monkeypatch):
    """測試成功的文字對話流程 (200 OK)。"""
    event = _make_event({
        "elder_id": "eld_001",
        "lang": "zh-TW",
        "text": "小助手，我今天已經吃過血壓藥了"
    })

    monkeypatch.setattr(auth, "assert_can_access_elder", lambda ev, eid: None)
    monkeypatch.setattr(
        chat,
        "invoke_agent_brain",
        lambda eid, txt: ("阿蘭嬤，太棒了！我已經幫您登記好血壓藥囉。", True)
    )
    monkeypatch.setattr(db, "save_conversation", lambda record: record)
    monkeypatch.setattr(chat.TTSFactory, "get_tts_engine", lambda lang: DummyTTS())
    monkeypatch.setattr(
        chat,
        "upload_audio_to_s3",
        lambda audio_bytes, conv_id: f"https://s3.example.com/tts/{conv_id}.mp3"
    )

    resp = chat.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    assert body["transcript"] == "小助手，我今天已經吃過血壓藥了"
    assert "太棒了" in body["reply_text"]
    assert body["routines_updated"] is True
    assert body["reply_audio_url"].startswith("https://s3.example.com/")
    assert body["conversation_id"].startswith("cnv_")
