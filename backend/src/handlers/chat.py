"""POST /chat — 對話核心。規格見 docs/api.md。

流程：
1. audio 輸入時先 ASR 轉文字（>60 秒回 400 AUDIO_TOO_LONG）
2. Bedrock 生成回覆（帶入記憶、當日行程、衛教知識庫 RAG；可回答自己的紀錄查詢）
3. 同一次對話擷取、分流入表：events / routines（建立或完成，含同步更新）/ memories
4. Polly TTS → S3 presigned URL（15 分鐘）
5. 回傳 conversation_id / transcript / reply_text / reply_audio_url / routines_updated
"""
from src.shared import responses


def handler(event, context):
    # TODO: 實作
    return responses.not_implemented()
