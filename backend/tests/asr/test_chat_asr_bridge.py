"""
chat handler 與 ASR 領域套件的串接測試。

只覆蓋 ASR 相關的路徑：錯誤碼對映、時間預算換算、內部診斷不外洩。
不呼叫 Bedrock、TTS、S3 或真實模型（Bedrock 與 TTS 以 monkeypatch 替換）。
"""
from __future__ import annotations

import base64
import importlib
import json
import struct

import boto3
import pytest
from moto import mock_aws

from src.handlers import chat
from src.shared.asr.composition import reset_asr_facade
from src.shared.asr.types import AsrErrorCategory, Transcript, TypedAsrError
from src.shared.tts import SynthesizedAudio
from src.shared.asr_http import (
    ASR_ERROR_HTTP_MAPPING,
    SERVER_SIDE_CATEGORIES,
    map_asr_error,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    """使用 main 的真實 turn/session 狀態機，ASR bridge 測試不得繞過 DynamoDB。"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_CONVERSATIONS", "conversations-asr-test")
    reset_asr_facade()
    monkeypatch.delenv("ASR_CONFIG_JSON", raising=False)

    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName="conversations-asr-test",
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
                        {
                            "AttributeName": "conversation_time_key",
                            "KeyType": "RANGE",
                        },
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "sessions-by-state",
                    "KeySchema": [
                        {"AttributeName": "session_state_key", "KeyType": "HASH"},
                        {
                            "AttributeName": "session_state_time_key",
                            "KeyType": "RANGE",
                        },
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        importlib.reload(importlib.import_module("src.shared.db"))
        importlib.reload(importlib.import_module("src.shared.sessions"))
        importlib.reload(importlib.import_module("src.shared.turns"))
        importlib.reload(chat)

        monkeypatch.setattr(
            chat,
            "invoke_agent_brain",
            lambda elder_id, transcript, lang="zh-TW", **kw: ("回覆", False),
        )

        class _FakeFacade:
            def synthesize(self, **kwargs):
                return SynthesizedAudio(b"mp3", "test_tts")

        monkeypatch.setattr(chat.db, "get_elder", lambda elder_id: {"elder_id": elder_id})
        monkeypatch.setattr(chat, "get_tts_facade", lambda: _FakeFacade())
        monkeypatch.setattr(
            chat, "upload_audio_to_s3", lambda audio_bytes, conversation_id: "tts/test.mp3"
        )
        monkeypatch.setattr(
            chat, "presign_audio", lambda object_key: "https://x" if object_key else None
        )
        yield

    reset_asr_facade()


def wav_bytes(duration_ms: int = 200) -> bytes:
    sample_rate = 16_000
    frames = int(sample_rate * duration_ms / 1000)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16
    )
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def call(body: dict, remaining_ms: int | None = 30_000) -> tuple[int, dict]:
    class _Context:
        def get_remaining_time_in_millis(self):
            return remaining_ms

    context = _Context() if remaining_ms is not None else None
    # 長者本人的 token：claims 帶 elder_id，authorizer 已驗證過。
    event = {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "cognito-sub-elder",
                    "elder_id": body.get("elder_id", "eld_a1b2c3d4e5f6"),
                }
            }
        },
    }
    response = chat.handler(event, context)
    parsed = json.loads(response["body"])
    # 失敗時把 body 印出來，否則 500 的斷言只會顯示狀態碼，看不出原因。
    if response["statusCode"] >= 500:
        print(f"[test] 5xx body = {parsed}")
    return response["statusCode"], parsed


def audio_body(fmt: str = "wav", lang: str = "hak", data: bytes | None = None) -> dict:
    payload = wav_bytes() if data is None else data
    return {
        "client_request_id": "req-1",
        "elder_id": "eld_a1b2c3d4e5f6",
        "lang": lang,
        "audio": {"data": base64.b64encode(payload).decode(), "format": fmt},
    }


# ─────────────────────────────────────────────────────────────────
# 錯誤碼對映表本身
# ─────────────────────────────────────────────────────────────────
def test_every_error_category_has_a_public_mapping() -> None:
    """新增錯誤分類卻忘記對映，必須在此被抓到。"""
    assert set(ASR_ERROR_HTTP_MAPPING) == set(AsrErrorCategory)


@pytest.mark.parametrize(
    ("category", "status", "code"),
    [
        (AsrErrorCategory.AUDIO_DURATION_EXCEEDED, 400, "AUDIO_TOO_LONG"),
        (AsrErrorCategory.INVALID_AUDIO, 400, "INVALID_PARAMETER"),
        (AsrErrorCategory.UNSUPPORTED_AUDIO_FORMAT, 400, "INVALID_PARAMETER"),
        (AsrErrorCategory.UNSUPPORTED_LANGUAGE, 400, "INVALID_PARAMETER"),
        (AsrErrorCategory.ROUTE_NOT_APPROVED, 500, "INTERNAL_ERROR"),
        (AsrErrorCategory.DEADLINE_EXCEEDED, 500, "INTERNAL_ERROR"),
        (AsrErrorCategory.CANCELLED, 500, "INTERNAL_ERROR"),
        (AsrErrorCategory.PROVIDER_UNAVAILABLE, 500, "INTERNAL_ERROR"),
        (AsrErrorCategory.PROVIDER_INVALID_RESPONSE, 500, "INTERNAL_ERROR"),
        (AsrErrorCategory.PROVIDER_FAILURE, 500, "INTERNAL_ERROR"),
    ],
)
def test_mapping_uses_only_codes_defined_in_api_contract(
    category, status, code
) -> None:
    """對映只能用 docs/api.md 既有的錯誤碼，公開契約不因後端換模型而變動。"""
    mapped = map_asr_error(category)
    assert (mapped.status_code, mapped.code) == (status, code)


def test_server_side_categories_are_the_5xx_ones() -> None:
    assert AsrErrorCategory.ROUTE_NOT_APPROVED in SERVER_SIDE_CATEGORIES
    assert AsrErrorCategory.INVALID_AUDIO not in SERVER_SIDE_CATEGORIES


# ─────────────────────────────────────────────────────────────────
# handler 端到端（ASR 段落）
# ─────────────────────────────────────────────────────────────────
def test_hak_audio_succeeds_via_default_mock_route() -> None:
    status, body = call(audio_body(lang="hak"))

    assert status == 200
    assert body["transcript"].strip() != ""
    assert body["conversation_id"].startswith("cnv_")
    assert body["session_id"].startswith("ses_")


def test_zh_tw_audio_is_fail_closed_as_internal_error() -> None:
    """預設設定下 zh-TW 未核准；對外只能是 500 INTERNAL_ERROR。"""
    status, body = call(audio_body(lang="zh-TW"))

    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"


def test_internal_diagnostic_message_is_not_exposed_to_caller(caplog) -> None:
    """route_not_approved 的內部原因（含佈署細節）不得出現在 response。"""
    _status, body = call(audio_body(lang="zh-TW"))

    message = body["error"]["message"]
    assert "capability gate" not in message
    assert "provider" not in message.lower()
    # 但必須留在伺服器日誌裡供排查
    assert "ASR failed" in caplog.text


def test_unsupported_audio_format_is_rejected_as_invalid_parameter() -> None:
    status, body = call(audio_body(fmt="mp3"))

    assert status == 400
    assert body["error"]["code"] == "INVALID_PARAMETER"


def test_corrupt_audio_is_rejected_as_invalid_parameter() -> None:
    status, body = call(audio_body(data=b"not really audio at all"))

    assert status == 400
    assert body["error"]["code"] == "INVALID_PARAMETER"


def test_oversized_audio_returns_audio_too_long() -> None:
    """長度判定來自 Canonical Audio 的精確時長，不是檔案大小猜測。"""
    status, body = call(audio_body(data=wav_bytes(duration_ms=60_001)))

    assert status == 400
    assert body["error"]["code"] == "AUDIO_TOO_LONG"


def test_sixty_second_audio_is_accepted() -> None:
    status, _body = call(audio_body(data=wav_bytes(duration_ms=60_000)))
    assert status == 200


def test_text_path_does_not_touch_asr(monkeypatch: pytest.MonkeyPatch) -> None:
    """text 輸入不應建立 ASR facade，設定有問題也不影響 text 路徑。"""
    monkeypatch.setenv("ASR_CONFIG_JSON", "{broken json")

    status, body = call(
        {
            "client_request_id": "req-2",
            "elder_id": "eld_a1b2c3d4e5f6",
            "lang": "zh-TW",
            "text": "我今天吃過藥了",
        }
    )

    assert status == 200
    assert body["transcript"] == "我今天吃過藥了"


def test_broken_asr_config_only_fails_the_audio_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASR_CONFIG_JSON", "{broken json")

    status, body = call(audio_body())

    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"


# ─────────────────────────────────────────────────────────────────
# 欄位驗證錯誤碼（api.md 用 INVALID_PARAMETER）
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "body",
    [
        {"lang": "hak", "text": "x"},
        {"elder_id": "eld_1", "text": "x"},
        {"elder_id": "eld_1", "lang": "en-US", "text": "x"},
        {"elder_id": "eld_1", "lang": "hak"},
        {"elder_id": "eld_1", "lang": "hak", "text": "x", "audio": {"data": "y"}},
    ],
)
def test_field_validation_uses_invalid_parameter_code(body) -> None:
    status, payload = call(body)
    assert status == 400
    assert payload["error"]["code"] == "INVALID_PARAMETER"


def test_blank_audio_data_uses_invalid_parameter_code() -> None:
    status, payload = call(
        {
            "elder_id": "eld_1",
            "lang": "hak",
            "audio": {"data": "", "format": "wav"},
        }
    )
    assert status == 400
    assert payload["error"]["code"] == "INVALID_PARAMETER"


# ─────────────────────────────────────────────────────────────────
# 時間預算
# ─────────────────────────────────────────────────────────────────
def test_budget_reserves_tail_for_downstream_steps() -> None:
    """ASR 不能吃掉整個 Lambda 剩餘時間，後面還有 Bedrock/TTS/S3。"""

    class _Context:
        def get_remaining_time_in_millis(self):
            return 15_000

    budget = chat.resolve_asr_budget_seconds(_Context())
    assert budget == pytest.approx(15.0 - chat.ASR_RESERVED_TAIL_SECONDS)


def test_budget_is_never_negative() -> None:
    class _AlmostExpired:
        def get_remaining_time_in_millis(self):
            return 500

    assert chat.resolve_asr_budget_seconds(_AlmostExpired()) == 0.0


def test_budget_falls_back_without_lambda_context() -> None:
    assert chat.resolve_asr_budget_seconds(None) == chat.ASR_DEFAULT_BUDGET_SECONDS


def test_budget_is_capped_by_default_budget() -> None:
    class _Generous:
        def get_remaining_time_in_millis(self):
            return 900_000

    assert (
        chat.resolve_asr_budget_seconds(_Generous())
        == chat.ASR_DEFAULT_BUDGET_SECONDS
    )


# ─────────────────────────────────────────────────────────────────
# 公開錯誤碼契約
# ─────────────────────────────────────────────────────────────────
def test_handler_only_emits_error_codes_defined_in_api_contract() -> None:
    """
    chat handler 不得自創錯誤碼。

    `docs/api.md` 是前後端唯一契約，`code` 是前端 UX 分支的穩定識別碼；
    出現契約外的碼，前端就沒有依據可以分支。
    """
    import re
    from pathlib import Path

    # docs/api.md 錯誤格式章節列出的全部 code
    allowed = {
        "INVALID_PARAMETER",
        "AUDIO_TOO_LONG",
        "ROUTINE_NOT_SCHEDULED",
        "FORBIDDEN",
        "ELDER_NOT_FOUND",
        "ROUTINE_NOT_FOUND",
        "SESSION_NOT_FOUND",
        "REQUEST_IN_PROGRESS",
        "IDEMPOTENCY_CONFLICT",
        "INTERNAL_ERROR",
    }

    source = Path(chat.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'responses\.error\(\s*\d+,\s*"([A-Z_]+)"', source))

    assert emitted, "should have found error codes in chat handler source"
    assert emitted <= allowed, f"契約外的錯誤碼：{sorted(emitted - allowed)}"


def test_server_errors_do_not_leak_exception_text() -> None:
    """5xx 訊息必須是固定文案；例外內容可能含 ARN、路徑等佈署細節。"""
    import re
    from pathlib import Path

    source = Path(chat.__file__).read_text(encoding="utf-8")
    interpolated = re.findall(
        r'responses\.error\(\s*5\d\d,\s*"[A-Z_]+",\s*f"', source
    )

    assert interpolated == [], "5xx 錯誤訊息不得用 f-string 內插例外內容"


# ─────────────────────────────────────────────────────────────────
# remote-only facade 端到端（Task 5）
# ─────────────────────────────────────────────────────────────────
def test_remote_endpoint_success_flows_through_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模擬遠端 endpoint 回傳文字後，聊天流程可繼續執行。"""
    from src.shared.asr.composition import build_facade, reset_asr_facade
    from src.shared.asr.facade import AsrFacade
    from src.shared.asr.types import Transcript
    import time

    class FakeRemoteFacade:
        """模擬已核准的 remote-only facade。"""

        def recognize(self, audio_bytes, input_format, language, deadline,
                      cancellation, context, hakka_dialect=None):
            return Transcript(text="遠端辨識成功的文字")

    # 替換 get_asr_facade 回傳我們的假 facade
    monkeypatch.setattr(
        "src.handlers.chat.get_asr_facade",
        lambda: FakeRemoteFacade(),
    )

    status, body = call(audio_body(lang="hak"))
    assert status == 200
    assert body["transcript"] == "遠端辨識成功的文字"


def test_remote_endpoint_route_not_approved_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """遠端 endpoint 路由未核准時，chat handler 回 500 INTERNAL_ERROR。"""
    from src.shared.asr.types import AsrErrorCategory, TypedAsrError

    class FailingFacade:
        def recognize(self, audio_bytes, input_format, language, deadline,
                      cancellation, context):
            return TypedAsrError(
                category=AsrErrorCategory.ROUTE_NOT_APPROVED,
                message="model production gate not approved",
                retryable=False,
            )

    monkeypatch.setattr(
        "src.handlers.chat.get_asr_facade",
        lambda: FailingFacade(),
    )

    status, body = call(audio_body(lang="zh-TW"))
    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    # 內部診斷訊息不外洩
    assert "production gate" not in body["error"]["message"]


def test_remote_endpoint_failure_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """遠端 endpoint 呼叫失敗時，chat handler 回 500 INTERNAL_ERROR。"""
    from src.shared.asr.types import AsrErrorCategory, TypedAsrError

    class EndpointErrorFacade:
        def recognize(self, audio_bytes, input_format, language, deadline,
                      cancellation, context):
            return TypedAsrError(
                category=AsrErrorCategory.PROVIDER_UNAVAILABLE,
                message="Endpoint temporarily unavailable",
                retryable=True,
            )

    monkeypatch.setattr(
        "src.handlers.chat.get_asr_facade",
        lambda: EndpointErrorFacade(),
    )

    status, body = call(audio_body(lang="hak"))
    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"


def test_remote_endpoint_deadline_exceeded_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """遠端 endpoint 逾時時，chat handler 回 500 INTERNAL_ERROR。"""
    from src.shared.asr.types import AsrErrorCategory, TypedAsrError

    class TimeoutFacade:
        def recognize(self, audio_bytes, input_format, language, deadline,
                      cancellation, context):
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Endpoint call timed out",
                retryable=True,
            )

    monkeypatch.setattr(
        "src.handlers.chat.get_asr_facade",
        lambda: TimeoutFacade(),
    )

    status, body = call(audio_body(lang="hak"))
    assert status == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
