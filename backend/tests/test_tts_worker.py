"""非同步 TTS worker 測試。

驗證重點是「昂貴且不可取消的合成不會被白做或做兩次」：重複投遞要能認出來、
永久性失敗不進重試迴圈、以及 key 一定在音訊真的存在之後才寫進 turn。
"""

from __future__ import annotations

import importlib
import json

import boto3
import pytest
from moto import mock_aws

from src.shared.tts import SynthesizedAudio, TtsErrorCategory, TypedTtsError

BUCKET = "e-hakka-care-audio"
TABLE_NAME = "e-hakka-care-conversations"
ELDER = "eld_1"
CONVERSATION = "cnv_1"
OBJECT_KEY = f"tts/{CONVERSATION}.mp3"


def _record(receive_count=None, **overrides):
    """組一則 SQS record；`receive_count` 對應 SQS 的 ApproximateReceiveCount。"""
    body = {
        "elder_id": ELDER,
        "conversation_id": CONVERSATION,
        "object_key": OBJECT_KEY,
        "text": "阿嬤早安，記得吃藥喔。",
        "language": "zh-TW",
        "dialect": None,
        "correlation_id": "corr-1",
    }
    body.update(overrides)
    record = {"messageId": "msg-1", "body": json.dumps(body, ensure_ascii=False)}
    if receive_count is not None:
        # SQS 的 attribute 值一律是字串。
        record["attributes"] = {"ApproximateReceiveCount": str(receive_count)}
    return record


class _Facade:
    """以固定結果驅動的假 facade；記錄呼叫次數以檢查重複投遞未重跑合成。"""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def synthesize(self, **kwargs):
        self.calls += 1
        return self.result


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", TABLE_NAME)
    monkeypatch.setenv("S3_AUDIO_BUCKET", BUCKET)

    with mock_aws():
        boto3.client("s3").create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )
        boto3.resource("dynamodb").create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "elder_id", "KeyType": "HASH"},
                {"AttributeName": "record_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "elder_id", "AttributeType": "S"},
                {"AttributeName": "record_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = importlib.reload(importlib.import_module("src.shared.db"))
        turns = importlib.reload(importlib.import_module("src.shared.turns"))
        module = importlib.reload(
            importlib.import_module("src.handlers.tts_worker")
        )
        yield module, turns

    for name in ("src.shared.db", "src.shared.turns", "src.handlers.tts_worker"):
        importlib.reload(importlib.import_module(name))


def _put_completed_turn(turns):
    boto3.resource("dynamodb").Table(TABLE_NAME).put_item(
        Item={
            "elder_id": ELDER,
            "record_id": turns.turn_record_id(CONVERSATION),
            "request_status": turns.STATUS_COMPLETED,
            "ai_respond_text": "阿嬤早安",
            "ai_respond_audio_pending": True,
        }
    )


def _stored_turn(turns):
    return turns.get_turn(ELDER, CONVERSATION)


def test_successful_synthesis_uploads_then_attaches_the_key(worker, monkeypatch):
    module, turns = worker
    _put_completed_turn(turns)
    facade = _Facade(SynthesizedAudio(b"mp3-bytes", "breezyvoice_remote"))
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    result = module.handler({"Records": [_record()]}, None)

    assert result == {"batchItemFailures": []}
    body = boto3.client("s3").get_object(Bucket=BUCKET, Key=OBJECT_KEY)["Body"].read()
    assert body == b"mp3-bytes"

    turn = _stored_turn(turns)
    assert turn["ai_respond_audio_s3_key"] == OBJECT_KEY
    assert turn["ai_respond_audio_pending"] is False


def test_duplicate_delivery_does_not_resynthesize(worker, monkeypatch):
    """合成一次是數十秒的 GPU 時間；SQS 至少投遞一次，重複的必須直接吞掉。"""
    module, turns = worker
    _put_completed_turn(turns)
    facade = _Facade(SynthesizedAudio(b"mp3-bytes", "breezyvoice_remote"))
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    module.handler({"Records": [_record()]}, None)
    module.handler({"Records": [_record()]}, None)

    assert facade.calls == 1


def test_retryable_failure_goes_back_to_the_queue(worker, monkeypatch):
    module, turns = worker
    _put_completed_turn(turns)
    facade = _Facade(
        TypedTtsError(TtsErrorCategory.PROVIDER_UNAVAILABLE, "endpoint down", True)
    )
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    result = module.handler({"Records": [_record(receive_count=1)]}, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}
    # 沒有音訊就不能留下 key，否則之後每次重播都是一條死連結
    assert "ai_respond_audio_s3_key" not in _stored_turn(turns)
    # 還有重試機會，就不能提早把 turn 標成沒有音訊——那句話還救得回來。
    assert _stored_turn(turns)["ai_respond_audio_pending"] is True


def test_final_attempt_clears_pending_but_still_reaches_the_dlq(worker, monkeypatch):
    """最後一次投遞失敗：turn 要收乾淨，訊息仍要進 DLQ。

    DLQ 沒有 consumer，pending 標記不在這裡收就再也沒人收——App 會一路等到 presigned
    URL 過期，畫面停在「正在準備聲音」。但訊息還是得進 DLQ，否則失敗證據就消失了。
    """
    module, turns = worker
    _put_completed_turn(turns)
    facade = _Facade(
        TypedTtsError(TtsErrorCategory.PROVIDER_UNAVAILABLE, "endpoint down", True)
    )
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    result = module.handler({"Records": [_record(receive_count=2)]}, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}
    turn = _stored_turn(turns)
    assert turn["ai_respond_audio_pending"] is False
    assert "ai_respond_audio_s3_key" not in turn


def test_route_not_approved_is_permanent_and_clears_pending(worker, monkeypatch):
    """gate 沒開時再投幾次都一樣；同時要把 pending 收掉，App 才不會一直等。"""
    module, turns = worker
    _put_completed_turn(turns)
    facade = _Facade(
        TypedTtsError(TtsErrorCategory.ROUTE_NOT_APPROVED, "not approved", False)
    )
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    result = module.handler({"Records": [_record()]}, None)

    assert result == {"batchItemFailures": []}
    turn = _stored_turn(turns)
    assert turn["ai_respond_audio_pending"] is False
    assert "ai_respond_audio_s3_key" not in turn


def test_malformed_message_is_not_retried(worker, monkeypatch):
    module, _ = worker
    facade = _Facade(SynthesizedAudio(b"mp3", "breezyvoice_remote"))
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    result = module.handler(
        {"Records": [{"messageId": "msg-1", "body": "{not json"}]}, None
    )

    assert result == {"batchItemFailures": []}
    assert facade.calls == 0


def test_message_missing_elder_id_is_not_retried(worker, monkeypatch):
    module, _ = worker
    facade = _Facade(SynthesizedAudio(b"mp3", "breezyvoice_remote"))
    monkeypatch.setattr(module, "get_tts_facade", lambda: facade)

    record = _record()
    body = json.loads(record["body"])
    del body["elder_id"]
    record["body"] = json.dumps(body)

    result = module.handler({"Records": [record]}, None)

    assert result == {"batchItemFailures": []}
    assert facade.calls == 0
