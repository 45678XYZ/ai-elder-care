"""POST /chat — 對話核心 Lambda Handler。

規格出處：docs/api.md 的 `POST /chat`；turn 三態與 session 接納規則見 docs/framework.md。

處理流程（順序固定：冪等判定先於任何 session 選擇、建立或 reserve）：
1. 解析與驗證傳入欄位 (client_request_id, elder_id, lang, text/audio 擇一)。
2. 進行 Cognito 授權驗證 (透過 shared/auth.py 的 assert_can_access_elder)。
3. 以 `elder_id + client_request_id` 推出穩定的 conversation_id 並強一致查既有 turn：
   內容不同回 409 IDEMPOTENCY_CONFLICT，已終態直接重播原結果，
   仍在飛且租約未到期回 409 REQUEST_IN_PROGRESS，租約到期才接管。
4. 全新請求才做 session 選擇（沿用可接納的 session 或建立新的），
   並以 transaction 建立 `processing` turn 並佔用 session 的 inflight 名額。
5. 若傳入音檔 (audio)，調用 CE ASR 將音訊轉成文字 transcript。
6. 調用 AWS Bedrock AgentCore (Claude 5 Sonnet) 大腦進行推導，帶入 sessionId=elder_id 以載入/更新託管 Memory，並處理 Tool Calling 觸發。
7. 依據 lang (zh-TW 或 hak) 調用 TTSFactory 生成語音 (中文 Polly / 客語 OmniVoice 帶 Fallback 降級防護)，並把 MP3 上傳至 S3。
8. 以 transaction 提交 `completed` 結果、解除 inflight 名額並把本輪追加進 session，
   再回傳符合 api.md 規範的 Response 200（音訊每次動態簽發 15 分鐘 presigned URL）。

任何一步失敗都把 turn 收成終態 `failed` 並保存穩定錯誤：留在 `processing` 會讓 session
永遠關不起來，離線萃取也就永遠不會跑。
"""

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Tuple

import boto3
from botocore.exceptions import ClientError

from pydantic import ValidationError

from src.shared import auth, db, responses, sessions, turns
from src.shared.models import ChatRequest, ConversationCreate
from src.shared.tts import TTSFactory
from src.shared.validation import RequestValidationError, validate


logger = logging.getLogger(__name__)

# 單句語音長度上限的防禦邊界（約 60 秒）
MAX_AUDIO_BYTES = 5 * 1024 * 1024


# 環境變數自訂名稱
S3_BUCKET_NAME = os.environ.get("S3_AUDIO_BUCKET", "ai-elder-care-audio")
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "")
BEDROCK_AGENT_ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "TSTALIASID")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
SAGEMAKER_CE_ENDPOINT_NAME = os.environ.get("SAGEMAKER_CE_ENDPOINT_NAME", "")
CE_ASR_API_URL = os.environ.get("CE_ASR_API_URL", "https://api.ce-asr.example.com/v1/transcribe")

# 全域 Boto3 Clients (Warm Start 重用連線)
_s3_client = None
_bedrock_agent_runtime = None
_sagemaker_runtime = None


def get_s3_client():
    """取得 S3 Client 實例。"""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def get_bedrock_agent_runtime():
    """取得 Bedrock Agent Runtime Client 實例。"""
    global _bedrock_agent_runtime
    if _bedrock_agent_runtime is None:
        _bedrock_agent_runtime = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    return _bedrock_agent_runtime


def get_sagemaker_runtime():
    """取得 SageMaker Runtime Client 實例。"""
    global _sagemaker_runtime
    if _sagemaker_runtime is None:
        _sagemaker_runtime = boto3.client("sagemaker-runtime", region_name=AWS_REGION)
    return _sagemaker_runtime


def parse_event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """解析 API Gateway event 中的 body 內容。"""
    body = event.get("body")
    if not body:
        return {}
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except Exception:
        raise ValueError("INVALID_JSON")


def transcribe_audio(audio_bytes: bytes, audio_format: str, lang: str) -> str:
    """呼叫 SageMaker CE 模型 Endpoint 或 CE ASR API 將語音轉寫為文字。

    優先檢查 SAGEMAKER_CE_ENDPOINT_NAME，若有設定則透過 boto3 呼叫 SageMaker；
    若未設定 Endpoint，則回傳 Mock 轉譯文字供測試。
    """
    # 檢查長度是否超過約 60 秒（以 5MB 音檔大小為防禦邊界）
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("AUDIO_TOO_LONG")

    # 1. 若配置了 SageMaker Endpoint，透過 boto3 原生呼叫 SageMaker 上的 CE 模型
    if SAGEMAKER_CE_ENDPOINT_NAME:
        try:
            sm_client = get_sagemaker_runtime()
            response = sm_client.invoke_endpoint(
                EndpointName=SAGEMAKER_CE_ENDPOINT_NAME,
                ContentType=f"audio/{audio_format}",
                Body=audio_bytes,
                CustomAttributes=f"lang={lang}"
            )
            result = json.loads(response["Body"].read().decode("utf-8"))
            return result.get("text", "")
        except Exception as e:
            print(f"[Error] SageMaker ASR invoke failed: {e}")
            raise RuntimeError(f"SageMaker ASR error: {e}")

    # 2. 未設定 Endpoint 時的保底 / Mock 文字（供開發測試期使用）
    if lang == "hak":
        return "阿頭仔，𠊎今晡日有食藥仔囉。"
    return "小助手，我今天已經吃過血壓藥了。"


def invoke_agent_brain(elder_id: str, transcript: str) -> Tuple[str, bool, bool]:
    """呼叫 AWS Bedrock AgentCore (Claude 5 Sonnet) 進行對話推導。
    
    Args:
        elder_id (str): 長者 ID，作為 sessionId 傳入以隔離託管 Memory。
        transcript (str): 長者輸入的對話文字。

    Returns:
        Tuple[str, bool, bool]: (reply_text, routines_updated, safety_alert_triggered)
    """
    if not BEDROCK_AGENT_ID:
        # 本地開發與未配置 Agent ID 時的保底回覆
        return (
            f"【模擬大腦回覆】收到您的訊息：「{transcript}」。已經幫您確認紀錄囉，請記得多喝水、按時休息！",
            False,
            False
        )

    client = get_bedrock_agent_runtime()
    try:
        # 產生帶有台灣時間 (UTC+8) 的當前時間前綴，讓 LLM 精準感知當前時間與星期
        tz_offset = 8 * 3600
        tw_time_tuple = time.gmtime(time.time() + tz_offset)
        weekdays = ["日", "一", "二", "三", "四", "五", "六"]
        wday_str = weekdays[int(time.strftime("%w", tw_time_tuple))]
        tw_time_str = time.strftime(f"%Y-%m-%d %H:%M (週{wday_str})", tw_time_tuple)

        prompt_with_time = f"[目前台灣時間: {tw_time_str}]\n{transcript}"

        response = client.invoke_agent_runtime(
            agentId=BEDROCK_AGENT_ID,
            agentAliasId=BEDROCK_AGENT_ALIAS_ID,
            sessionId=elder_id,
            inputText=prompt_with_time
        )

        reply_text = ""
        routines_updated = False
        safety_alert_triggered = False

        # 讀取 completion 事件串流
        for event in response.get("completion", []):
            if "chunk" in event:
                chunk_bytes = event["chunk"]["bytes"]
                reply_text += chunk_bytes.decode("utf-8")
            
            # 檢查 Response Trace 是否觸發了 routines 相關工具或通知工具
            if "trace" in event:
                trace_str = str(event["trace"])
                if any(tool in trace_str for tool in ("complete_routine", "create_routine", "update_routine", "deactivate_routine")):
                    routines_updated = True
                if "notify_caregiver" in trace_str:
                    safety_alert_triggered = True

        if not reply_text:
            reply_text = "抱歉，我剛才沒有聽清，您可以再說一次嗎？"

        return reply_text, routines_updated, safety_alert_triggered

    except ClientError as e:
        print(f"[Error] Bedrock Agent invoke failed: {e.response['Error']['Message']}")
        raise RuntimeError(f"Bedrock Agent invoke error: {e.response['Error']['Message']}")


def upload_audio_to_s3(audio_bytes: bytes, conversation_id: str) -> str | None:
    """將 TTS 合成之 MP3 上傳至 S3，回傳 object key；上傳失敗回 None。

    只回 key 不回 URL：presigned URL 有 15 分鐘效期，存進 DynamoDB 之後重播就是一條死連結，
    因此 turn 只保存 key，每次回應再簽發（見 docs/framework.md 的音訊欄位規則）。

    失敗必須回 None 而不是照樣回 key：turn 一旦帶著 key 提交成 completed，那個 key 就是
    永久的——之後每次重播都會簽出一條指向不存在物件的連結，長者永遠聽不到這句回覆。
    """
    object_key = f"tts/{conversation_id}.mp3"
    try:
        get_s3_client().put_object(
            Bucket=S3_BUCKET_NAME,
            Key=object_key,
            Body=audio_bytes,
            ContentType="audio/mpeg"
        )
    except Exception:
        logger.exception("回覆語音上傳失敗，本輪不附音檔：conversation_id=%s", conversation_id)
        return None
    return object_key


def presign_audio(object_key: str | None) -> str | None:
    """簽發 15 分鐘有效的 Presigned URL；沒有音檔或簽發失敗回 None。

    簽不出來時不回未簽名的 URL：那條連結一定會被 S3 擋掉，App 只會拿到一個播不出來的網址，
    還不如明確地沒有音檔、直接走文字降級。
    """
    if not object_key:
        return None
    try:
        return get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": object_key},
            ExpiresIn=900  # 15 分鐘有效
        )
    except Exception:
        logger.exception("簽發回覆語音 URL 失敗：object_key=%s", object_key)
        return None


class ImmediateResponse(Exception):
    """流程中途就已確定的回應：冪等重播、idempotency 衝突或仍在處理中。"""

    def __init__(self, response: Dict[str, Any]):
        super().__init__(response.get("body", ""))
        self.response = response


class TurnFailure(Exception):
    """turn 已 reserve 之後才發生的失敗；帶著要保存並回傳的穩定錯誤。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """POST /chat 對話核心進入點。"""
    try:
        # 1. 解析與使用 Pydantic 統一校驗 Request Body
        try:
            body = parse_event_body(event)
        except ValueError:
            return responses.error(400, "INVALID_JSON", "請求內文不是有效的 JSON 格式")

        req = validate(ChatRequest, body)

        # 2. 身份與存取權限驗證
        auth.assert_can_access_elder(event, req.elder_id)

        # 3. 冪等判定：同一個 client_request_id 永遠對應同一個 turn
        audio_bytes = decode_audio(req)
        owner = lease_owner(context)
        conversation_id = turns.conversation_id_for(req.elder_id, req.client_request_id)
        session_id = resume_or_accept(req, conversation_id, request_hash(req, audio_bytes), owner)

        # 4. 副作用區：turn 已是 processing，之後每一條路徑都必須把它收成終態
        try:
            transcript, reply_text, routines_updated, safety_alert_triggered, audio_key = run_turn(
                req, audio_bytes, conversation_id
            )
        except TurnFailure as failure:
            return fail_turn(req.elder_id, conversation_id, session_id, owner, failure)
        except Exception:
            logger.exception("對話處理失敗：conversation_id=%s", conversation_id)
            return fail_turn(
                req.elder_id,
                conversation_id,
                session_id,
                owner,
                TurnFailure(500, "INTERNAL_ERROR", "內部系統錯誤"),
            )

        # 5. 以單一 transaction 提交終態結果並把本輪追加進 session
        try:
            rt_labels = []
            if routines_updated:
                rt_labels.append("routine")
            if safety_alert_triggered:
                rt_labels.append("safety_alert")
            if not rt_labels:
                rt_labels.append("none")

            committed = turns.commit(
                req.elder_id,
                conversation_id,
                session_id=session_id,
                owner=owner,
                result={
                    "elder_transcript": transcript,
                    "ai_respond_text": reply_text,
                    "ai_respond_audio_s3_key": audio_key,
                    "routines_updated": routines_updated,
                    "rt_labels": rt_labels,
                },
            )
        except turns.TurnError:
            # 結果沒能落地就不能回 200：回 500 讓 App 以相同 client_request_id 重試，
            # 租約到期後接管者會重跑並收斂，不會產生第二輪對話
            logger.exception("提交對話結果失敗：conversation_id=%s", conversation_id)
            return responses.error(500, "INTERNAL_ERROR", "對話結果寫入失敗")

        return chat_response(committed)

    except (auth.AuthError, RequestValidationError, ImmediateResponse) as exc:
        return exc.response

    except Exception:
        # 例外訊息只進 log：回給 App 的內容是穩定的公開錯誤，不夾帶內部細節
        logger.exception("未預期的錯誤")
        return responses.error(500, "INTERNAL_ERROR", "內部系統錯誤")



def lease_owner(context: Any) -> str:
    """本次執行的 request lease 擁有者；用於分辨「自己續約」與「他人接管」。"""
    return getattr(context, "aws_request_id", None) or f"local-{uuid.uuid4().hex[:12]}"


def decode_audio(req: ChatRequest) -> bytes:
    """解出音檔位元組並做長度防禦；文字輸入回空 bytes。

    在 reserve 之前做完：這些是純輸入驗證，沒必要為了回 400 而先佔用 session 的名額。
    """
    if not req.audio:
        return b""
    if not req.audio.data:
        raise RequestValidationError(
            responses.error(400, "INVALID_PARAMETER", "audio.data 不可為空")
        )
    try:
        audio_bytes = base64.b64decode(req.audio.data)
    except Exception:
        raise RequestValidationError(
            responses.error(400, "INVALID_PARAMETER", "audio.data 解碼失敗，非有效 base64 字串")
        )
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise RequestValidationError(
            responses.error(400, "AUDIO_TOO_LONG", "單句語音長度超過 60 秒限制")
        )
    return audio_bytes


def request_hash(req: ChatRequest, audio_bytes: bytes) -> str:
    """本次請求內容的正規化 hash，用來分辨「重送」與「同一個 ID 換了內容」。

    不含 `session_id`：那是 routing 的提示而非請求內容，App 在重試時帶了新的 session_id
    不該被判成 idempotency conflict。音檔取 digest，避免把整段 base64 讀進 hash。
    """
    return turns.request_hash(
        {
            "elder_id": req.elder_id,
            "lang": req.lang,
            "input_type": "audio" if req.audio else "text",
            "content": req.text or hashlib.sha256(audio_bytes).hexdigest(),
        }
    )


def resume_or_accept(
    req: ChatRequest, conversation_id: str, payload_hash: str, owner: str
) -> str:
    """回傳本 turn 所屬的 session ID；已終態或仍在飛的請求在此直接結束流程。"""
    existing = turns.get_turn(req.elder_id, conversation_id)
    if existing is None:
        return accept_new_turn(req, conversation_id, payload_hash, owner)

    try:
        turns.assert_same_request(existing, payload_hash)
    except turns.TurnConflictError as exc:
        raise ImmediateResponse(responses.error(409, "IDEMPOTENCY_CONFLICT", str(exc)))

    if existing.get("request_status") in turns.TERMINAL_STATUSES:
        raise ImmediateResponse(replay(existing))
    if not turns.is_lease_expired(existing):
        raise ImmediateResponse(in_progress())

    try:
        # 租約到期代表前一個 invocation 已死；接管原 turn 與原 reservation，不重做 routing
        return turns.take_over(req.elder_id, conversation_id, owner=owner)["session_id"]
    except turns.TurnTransitionRejectedError:
        current = turns.get_turn(req.elder_id, conversation_id) or {}
        if current.get("request_status") in turns.TERMINAL_STATUSES:
            raise ImmediateResponse(replay(current))
        raise ImmediateResponse(in_progress())


def accept_new_turn(
    req: ChatRequest, conversation_id: str, payload_hash: str, owner: str
) -> str:
    """全新請求才走 session 選擇與 reserve。"""
    session = requested_session(req)
    turn = {
        "conversation_id": conversation_id,
        "idempotency_key": req.client_request_id,
        "client_request_id": req.client_request_id,
        "request_hash": payload_hash,
        "lang": req.lang,
        "input_type": "audio" if req.audio else "text",
        "source": "elder_initiated",
    }

    # 最多兩輪：指定的 session 可能在讀取後才被 close 或塞滿，這時改用新的 active session。
    # 新建的 session 不會有第二個競爭者，因此不需要無上限重試。
    for _ in range(2):
        if session is None:
            session = sessions.create_session(req.elder_id)
        try:
            turns.reserve(req.elder_id, session["session_id"], turn=turn, owner=owner)
            return session["session_id"]
        except turns.TurnReserveRejectedError:
            session = None
        except turns.TurnInProgressError:
            # 併發的相同請求先一步建立了 turn，交回冪等判定處置
            raise ImmediateResponse(in_progress())

    logger.error("無法接納新的 turn：conversation_id=%s", conversation_id)
    raise ImmediateResponse(responses.error(500, "INTERNAL_ERROR", "無法建立對話 session"))


def requested_session(req: ChatRequest) -> Dict[str, Any] | None:
    """取出可沿用的 session；不能沿用時回 None（由呼叫端建立新的）。"""
    if not req.session_id:
        return None
    session = sessions.get_session(req.elder_id, req.session_id)
    if session is None:
        # 不屬於該長者的 session 在此同樣讀不到；一律回 404，不以 403 洩漏 session 是否存在
        raise ImmediateResponse(
            responses.error(404, "SESSION_NOT_FOUND", "找不到指定的 session")
        )
    return session if sessions.can_accept(session) else None


def run_turn(
    req: ChatRequest, audio_bytes: bytes, conversation_id: str
) -> Tuple[str, str, bool, bool, str | None]:
    """ASR → 對話大腦 → TTS → 上傳；回 (transcript, reply_text, routines_updated, safety_alert_triggered, audio key)。

    音檔存不進 S3 時 audio key 為 None，本輪仍然成立：回覆內容與已提交的 routine 副作用都是
    真的，把整輪判成失敗只會逼長者再講一次，反而可能讓對話產生的 routine 被重複建立。
    """
    if req.text:
        transcript = req.text
    else:
        audio_format = req.audio.format if req.audio else "m4a"
        try:
            transcript = transcribe_audio(audio_bytes, audio_format, req.lang)
        except ValueError:
            raise TurnFailure(400, "TRANSCRIPTION_FAILED", "語音轉寫失敗")
        except Exception:
            raise TurnFailure(500, "TRANSCRIPTION_FAILED", "語音轉寫服務暫時無法使用")

    try:
        reply_text, routines_updated, safety_alert_triggered = invoke_agent_brain(req.elder_id, transcript)
    except Exception:
        raise TurnFailure(500, "BEDROCK_ERROR", "呼叫對話大腦失敗")

    try:
        audio_reply = TTSFactory.get_tts_engine(req.lang).synthesize(reply_text)
    except Exception:
        raise TurnFailure(500, "TTS_FAILED", "語音合成失敗")

    return transcript, reply_text, routines_updated, safety_alert_triggered, upload_audio_to_s3(
        audio_reply, conversation_id
    )


def fail_turn(
    elder_id: str,
    conversation_id: str,
    session_id: str,
    owner: str,
    failure: TurnFailure,
) -> Dict[str, Any]:
    """把 turn 收成終態 failed 並回傳該錯誤。

    錯誤訊息會被持久化並在重送時原樣重播，因此只放穩定、安全化的內容，不夾帶模型輸出、
    逐字稿或內部例外訊息。
    """
    try:
        turns.fail(
            elder_id,
            conversation_id,
            session_id=session_id,
            code=failure.code,
            message=failure.message,
            http_status=failure.status,
            owner=owner,
        )
    except turns.TurnError:
        # 收不成終態就交給 closer 的 inflight 收斂；這裡仍要把錯誤回給 App
        logger.exception("標記 turn 失敗未成立：conversation_id=%s", conversation_id)
    return responses.error(failure.status, failure.code, failure.message)


def replay(turn: Dict[str, Any]) -> Dict[str, Any]:
    """重播既有終態結果：completed 回原業務結果，failed 回首次持久化的穩定錯誤。"""
    if turn.get("request_status") == turns.STATUS_FAILED:
        return responses.error(
            int(turn.get("error_http_status") or 500),
            turn.get("error_code") or "INTERNAL_ERROR",
            turn.get("error_message") or "對話處理失敗",
        )
    return chat_response(turn)


def in_progress() -> Dict[str, Any]:
    return responses.error(
        409, "REQUEST_IN_PROGRESS", "同一請求仍在處理中，請以相同 client_request_id 重試"
    )


def chat_response(turn: Dict[str, Any]) -> Dict[str, Any]:
    """docs/api.md 規範的 flat response；音訊每次重新簽發 presigned URL。"""
    return responses.json_response(
        200,
        {
            "conversation_id": turn["conversation_id"],
            "session_id": turn.get("session_id"),
            "transcript": turn.get("elder_transcript") or "",
            "reply_text": turn.get("ai_respond_text") or "",
            "reply_audio_url": presign_audio(turn.get("ai_respond_audio_s3_key")),
            "routines_updated": bool(turn.get("routines_updated")),
        },
    )

