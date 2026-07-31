"""POST /chat — 對話核心 Lambda Handler。

規格出處：docs/api.md

處理流程：
1. 解析與驗證傳入欄位 (elder_id, lang, text/audio 擇一)。
2. 進行 Cognito 授權驗證 (透過 shared/auth.py 的 assert_can_access_elder)。
3. 若傳入音檔 (audio)，交由 ASR 領域套件 (shared/asr) 轉成文字 transcript。
   音訊長度、格式與解碼判定全部由 Canonical Audio 邊界負責，本 handler 只做
   base64 解碼與錯誤碼對映 (見 shared/asr_http.py)。
4. 調用 AWS Bedrock AgentCore (Claude 5 Sonnet) 大腦進行推導，帶入 sessionId=elder_id 以載入/更新託管 Memory，並處理 Tool Calling 觸發。
5. 產生唯一的 conversation_id，並將對話歷史雙寫至 DynamoDB conversations 表 (提供給隊友的 summary 模組使用)。
6. 依據 lang (zh-TW 或 hak) 調用 TTSFactory 生成語音 (中文 Polly / 客語 OmniVoice 帶 Fallback 降級防護)。
7. 將 MP3 上傳至 S3 儲存桶並取得 15 分鐘有效的 Presigned URL (reply_audio_url)。
8. 回傳符合 api.md 規範的 Response 200。
"""

import base64
import json
import os
import time
import uuid
from typing import Any, Dict, Tuple

import boto3
from botocore.exceptions import ClientError

from src.shared import auth, db, responses
from src.shared.asr.composition import get_asr_facade
from src.shared.asr.config import ConfigParseError
from src.shared.asr.types import (
    AsrErrorCategory,
    CancellationSignal,
    CorrelationContext,
    Deadline,
    InputFormat,
    Language,
    Transcript,
    TypedAsrError,
)
from src.shared.asr_http import SERVER_SIDE_CATEGORIES, map_asr_error
from src.shared.tts import TTSFactory

# 環境變數自訂名稱
S3_BUCKET_NAME = os.environ.get("S3_AUDIO_BUCKET", "ai-elder-care-audio")
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "")
BEDROCK_AGENT_ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "TSTALIASID")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

# ASR 之後還要跑 Bedrock、TTS 與 S3 上傳，所以不能把 Lambda 剩餘時間全給 ASR。
# 這個保留值是給後續步驟的餘裕；剩餘時間不足時 ASR 會自己回 deadline_exceeded。
ASR_RESERVED_TAIL_SECONDS = float(os.environ.get("ASR_RESERVED_TAIL_SECONDS", "8"))
# 本機或無 Lambda context 時的 ASR 預算上限。
ASR_DEFAULT_BUDGET_SECONDS = float(os.environ.get("ASR_DEFAULT_BUDGET_SECONDS", "20"))

# 全域 Boto3 Clients (Warm Start 重用連線)
_s3_client = None
_bedrock_agent_runtime = None


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
        _bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
    return _bedrock_agent_runtime


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


def resolve_asr_budget_seconds(context: Any) -> float:
    """
    算出這次 ASR 可用的時間預算。

    以 Lambda 剩餘時間扣掉後續 Bedrock／TTS／S3 所需的餘裕；取不到 context
    （本機、單元測試）時退回固定上限。
    """
    remaining_ms = None
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if callable(getter):
        try:
            remaining_ms = getter()
        except Exception:
            remaining_ms = None

    if not isinstance(remaining_ms, (int, float)):
        return ASR_DEFAULT_BUDGET_SECONDS

    budget = remaining_ms / 1000.0 - ASR_RESERVED_TAIL_SECONDS
    return max(0.0, min(budget, ASR_DEFAULT_BUDGET_SECONDS))


def transcribe_audio(
    audio_bytes: bytes,
    audio_format: str,
    lang: str,
    correlation_id: str,
    budget_seconds: float,
) -> Transcript | TypedAsrError:
    """
    以 ASR 領域套件 (shared/asr) 將語音轉寫為文字。

    長度上限、格式驗證、解碼與備援全部由套件內部負責，本函式只做型別轉換。
    回傳領域型別而非字串，讓呼叫端能依錯誤分類決定 HTTP 對映。
    """
    try:
        input_format = InputFormat.from_str(audio_format)
    except ValueError:
        return TypedAsrError(
            category=AsrErrorCategory.UNSUPPORTED_AUDIO_FORMAT,
            message=f"Unsupported audio format: {audio_format!r}.",
            retryable=False,
        )

    language = Language.from_str(lang)

    facade = get_asr_facade()
    return facade.recognize(
        audio_bytes=audio_bytes,
        input_format=input_format,
        language=language,
        deadline=Deadline.after(budget_seconds, time.monotonic),
        cancellation=CancellationSignal(),
        context=CorrelationContext(correlation_id=correlation_id),
    )


def invoke_agent_brain(elder_id: str, transcript: str) -> Tuple[str, bool]:
    """呼叫 AWS Bedrock AgentCore (Claude 5 Sonnet) 進行對話推導。
    
    Args:
        elder_id (str): 長者 ID，作為 sessionId 傳入以隔離託管 Memory。
        transcript (str): 長者輸入的對話文字。

    Returns:
        Tuple[str, bool]: (reply_text, routines_updated)
    """
    if not BEDROCK_AGENT_ID:
        # 本地開發與未配置 Agent ID 時的保底回覆
        return (
            f"【模擬大腦回覆】收到您的訊息：「{transcript}」。已經幫您確認紀錄囉，請記得多喝水、按時休息！",
            False
        )

    client = get_bedrock_agent_runtime()
    try:
        response = client.invoke_agent(
            agentId=BEDROCK_AGENT_ID,
            agentAliasId=BEDROCK_AGENT_ALIAS_ID,
            sessionId=elder_id,
            inputText=transcript
        )

        reply_text = ""
        routines_updated = False

        # 讀取 completion 事件串流
        for event in response.get("completion", []):
            if "chunk" in event:
                chunk_bytes = event["chunk"]["bytes"]
                reply_text += chunk_bytes.decode("utf-8")
            
            # 檢查 Response Trace 是否觸發了 routines 相關工具
            if "trace" in event:
                trace_str = str(event["trace"])
                if "complete_routine" in trace_str or "create_routine" in trace_str:
                    routines_updated = True

        if not reply_text:
            reply_text = "抱歉，我剛才沒有聽清，您可以再說一次嗎？"

        return reply_text, routines_updated

    except ClientError as e:
        print(f"[Error] Bedrock Agent invoke failed: {e.response['Error']['Message']}")
        raise RuntimeError(f"Bedrock Agent invoke error: {e.response['Error']['Message']}")


def upload_audio_to_s3(audio_bytes: bytes, conversation_id: str) -> str:
    """將 TTS 合成之 MP3 上傳至 S3 儲存桶，並發行 15 分鐘有效的 Presigned URL。"""
    s3 = get_s3_client()
    object_key = f"tts/{conversation_id}.mp3"

    try:
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=object_key,
            Body=audio_bytes,
            ContentType="audio/mpeg"
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": object_key},
            ExpiresIn=900  # 15 分鐘有效
        )
        return url
    except Exception as e:
        print(f"[Warning] S3 upload or presigned URL generation failed: {e}")
        # 當開發環境無 S3 存取權時的回傳預設 URL
        return f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{object_key}"


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """POST /chat 對話核心進入點。"""
    try:
        # 1. 解析 Request Body
        try:
            body = parse_event_body(event)
        except ValueError:
            # api.md 的錯誤碼表沒有 INVALID_JSON；格式錯誤歸在 INVALID_PARAMETER。
            return responses.error(
                400, "INVALID_PARAMETER", "請求內文不是有效的 JSON 格式"
            )

        elder_id = body.get("elder_id")
        lang = body.get("lang")
        text = body.get("text")
        audio = body.get("audio")
        client_request_id = body.get("client_request_id")
        session_id = body.get("session_id")

        # 2. 驗證必要欄位 (依據 docs/api.md 契約)
        if not elder_id:
            return responses.error(400, "INVALID_PARAMETER", "缺少必填欄位 elder_id")
        if not lang or lang not in ("zh-TW", "hak"):
            return responses.error(
                400, "INVALID_PARAMETER", "lang 欄位必填且必須為 zh-TW 或 hak"
            )
        if not text and not audio:
            return responses.error(
                400, "INVALID_PARAMETER", "text 與 audio 必須擇一填寫"
            )
        if text and audio:
            return responses.error(
                400, "INVALID_PARAMETER", "text 與 audio 不能同時提供"
            )

        # 3. 身份與存取權限驗證
        try:
            auth.assert_can_access_elder(event, elder_id)
        except auth.AuthError as auth_err:
            return auth_err.response
        except (AttributeError, NotImplementedError):
            # 若授權模組在開發測試期尚未綁定，則先放行
            pass

        # 4. 取得辨識文字 (transcript)
        if text:
            transcript = text
        else:
            # 處理音檔辨識
            audio_b64 = audio.get("data", "")
            audio_fmt = audio.get("format", "m4a")
            if not audio_b64:
                return responses.error(
                    400, "INVALID_PARAMETER", "audio.data 不可為空"
                )

            try:
                audio_bytes = base64.b64decode(audio_b64)
            except Exception:
                return responses.error(
                    400, "INVALID_PARAMETER", "audio.data 解碼失敗，非有效 base64 字串"
                )

            # correlation id 必須是不透明值：不可由 elder_id 或 session_id 推導，
            # 否則遙測就等於帶上了長者識別資訊。
            correlation_id = client_request_id or f"corr_{uuid.uuid4().hex}"

            try:
                asr_result = transcribe_audio(
                    audio_bytes=audio_bytes,
                    audio_format=audio_fmt,
                    lang=lang,
                    correlation_id=correlation_id,
                    budget_seconds=resolve_asr_budget_seconds(context),
                )
            except ConfigParseError as cfg_err:
                # ASR 設定有問題只影響音訊路徑；text 路徑不受影響。
                print(f"[Error] ASR configuration rejected: {cfg_err}")
                return responses.error(
                    500, "INTERNAL_ERROR", "語音辨識服務目前無法使用"
                )

            if isinstance(asr_result, TypedAsrError):
                mapped = map_asr_error(asr_result.category)
                # 內部診斷訊息只進日誌，不回給呼叫端。
                if asr_result.category in SERVER_SIDE_CATEGORIES:
                    print(
                        f"[Error] ASR failed: category={asr_result.category.value} "
                        f"correlation_id={correlation_id} detail={asr_result.message}"
                    )
                return responses.error(
                    mapped.status_code, mapped.code, mapped.message
                )

            transcript = asr_result.text

        # 5. 調用 Bedrock AgentCore 大腦
        try:
            reply_text, routines_updated = invoke_agent_brain(elder_id, transcript)
        except Exception as e:
            # 內部原因只進日誌：例外訊息可能含 model ID、ARN 等佈署細節。
            print(f"[Error] Bedrock Agent invoke failed: {e}")
            return responses.error(500, "INTERNAL_ERROR", "對話服務目前無法使用")

        # 6. 產生獨一無二的對話 ID
        conversation_id = f"cnv_{uuid.uuid4().hex[:12]}"
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        # TODO: session 生命週期尚未實作。docs/api.md 要求後端依 idle 門檻、turn
        # 上限與 closing/closed 狀態決定沿用或新建 session，並在 reserve/replay
        # 之前先做 client_request_id 冪等判定。目前只回傳形狀正確的 session_id：
        # 帶了就沿用、沒帶就新建，沒有狀態機、沒有冪等、沒有 inflight reserve。
        if not session_id:
            session_id = f"ses_{uuid.uuid4().hex[:12]}"

        # 7. 雙寫對話紀錄至 DynamoDB conversations 表 (確保隊友摘要 API 的相容性)
        try:
            conv_record = {
                "conversation_id": conversation_id,
                "elder_id": elder_id,
                "ts": now_ts,
                "lang": lang,
                "transcript": transcript,
                "reply_text": reply_text,
                "routines_updated": routines_updated
            }
            db.save_conversation(conv_record)
        except Exception as db_err:
            print(f"[Warning] 寫入對話紀錄至 DynamoDB 失敗: {db_err}")

        # 8. 語音合成 (TTS Factory - 中文用 Polly，客語用 OmniVoice 帶 Fallback)
        try:
            tts_engine = TTSFactory.get_tts_engine(lang)
            audio_bytes = tts_engine.synthesize(reply_text)
        except Exception as tts_err:
            print(f"[Error] TTS Synthesis failed: {tts_err}")
            return responses.error(500, "INTERNAL_ERROR", "語音合成失敗")

        # 9. 上傳 MP3 至 S3 並取得預簽名 URL
        reply_audio_url = upload_audio_to_s3(audio_bytes, conversation_id)

        # 10. 回傳符合 docs/api.md 規範的 Response 200
        return responses.json_response(200, {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "transcript": transcript,
            "reply_text": reply_text,
            "reply_audio_url": reply_audio_url,
            "routines_updated": routines_updated
        })

    except Exception as err:
        print(f"[Unhandled Error] {err}")
        return responses.error(500, "INTERNAL_ERROR", "內部系統錯誤")
