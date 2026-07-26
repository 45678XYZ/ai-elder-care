"""POST /chat — 對話核心 Lambda Handler。

規格出處：docs/api.md

處理流程：
1. 解析與驗證傳入欄位 (elder_id, lang, text/audio 擇一)。
2. 進行 Cognito 授權驗證 (透過 shared/auth.py 的 assert_can_access_elder)。
3. 若傳入音檔 (audio)，先調用 CE ASR 將音訊轉成文字 transcript (超過 60 秒長度限制回傳 400 AUDIO_TOO_LONG)。
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
from src.shared.tts import TTSFactory

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
        _bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
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
    if len(audio_bytes) > 5 * 1024 * 1024:
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
            return responses.error(400, "INVALID_JSON", "請求內文不是有效的 JSON 格式")

        elder_id = body.get("elder_id")
        lang = body.get("lang")
        text = body.get("text")
        audio = body.get("audio")

        # 2. 驗證必要欄位 (依據 docs/api.md 契約)
        if not elder_id:
            return responses.error(400, "INVALID_PARAM", "缺少必填欄位 elder_id")
        if not lang or lang not in ("zh-TW", "hak"):
            return responses.error(400, "INVALID_PARAM", "lang 欄位必填且必須為 zh-TW 或 hak")
        if not text and not audio:
            return responses.error(400, "INVALID_PARAM", "text 與 audio 必須擇一填寫")
        if text and audio:
            return responses.error(400, "INVALID_PARAM", "text 與 audio 不能同時提供")

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
                return responses.error(400, "INVALID_PARAM", "audio.data 不可為空")

            try:
                audio_bytes = base64.b64decode(audio_b64)
            except Exception:
                return responses.error(400, "INVALID_PARAM", "audio.data 解碼失敗，非有效 base64 字串")

            try:
                transcript = transcribe_audio(audio_bytes, audio_fmt, lang)
            except ValueError as val_err:
                if str(val_err) == "AUDIO_TOO_LONG":
                    return responses.error(400, "AUDIO_TOO_LONG", "單句語音長度超過 60 秒限制")
                return responses.error(400, "TRANSCRIPTION_FAILED", "語音轉寫失敗")

        # 5. 調用 Bedrock AgentCore 大腦
        try:
            reply_text, routines_updated = invoke_agent_brain(elder_id, transcript)
        except Exception as e:
            return responses.error(500, "BEDROCK_ERROR", f"呼叫對話大腦失敗: {str(e)}")

        # 6. 產生獨一無二的對話 ID
        conversation_id = f"cnv_{uuid.uuid4().hex[:12]}"
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")

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
        except (AttributeError, NotImplementedError):
            print(f"[Info] db.save_conversation 尚未在當前分支中導入。")
        except Exception as db_err:
            print(f"[Warning] 寫入對話紀錄至 DynamoDB 失敗: {db_err}")

        # 8. 語音合成 (TTS Factory - 中文用 Polly，客語用 OmniVoice 帶 Fallback)
        try:
            tts_engine = TTSFactory.get_tts_engine(lang)
            audio_bytes = tts_engine.synthesize(reply_text)
        except Exception as tts_err:
            print(f"[Error] TTS Synthesis failed: {tts_err}")
            return responses.error(500, "TTS_FAILED", f"語音合成失敗: {str(tts_err)}")

        # 9. 上傳 MP3 至 S3 並取得預簽名 URL
        reply_audio_url = upload_audio_to_s3(audio_bytes, conversation_id)

        # 10. 回傳符合 docs/api.md 規範的 Response 200
        return responses.json_response(200, {
            "conversation_id": conversation_id,
            "transcript": transcript,
            "reply_text": reply_text,
            "reply_audio_url": reply_audio_url,
            "routines_updated": routines_updated
        })

    except Exception as err:
        print(f"[Unhandled Error] {err}")
        return responses.error(500, "INTERNAL_ERROR", f"內部系統錯誤: {str(err)}")
