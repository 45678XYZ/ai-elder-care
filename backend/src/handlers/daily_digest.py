"""每日晚報 Lambda Handler — 每晚 22:00 (台灣時間) 由 EventBridge Scheduler 觸發。

觸發流程：
1. EventBridge Scheduler (cron 0 14 * * ? *) 觸發本 Lambda。
2. 掃描 elders 表，取得所有長者及其綁定的照護者 caregiver_ids。
3. 對每位長者：
   a. 從 daily_summaries 表取得今日健康摘要。
   b. 從 routines + events 表計算今日例行行程的完成狀況。
   c. 組裝日報 Email 內文，透過 SNS 推播至照護者信箱。
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import boto3

from src.shared import db

logger = logging.getLogger(__name__)

# 台灣時區 (+08:00)
TZ_TAIPEI = timezone(timedelta(hours=8))

# 環境變數
CAREGIVER_NOTIFY_TOPIC_ARN = os.environ.get("CAREGIVER_NOTIFY_TOPIC_ARN", "")
AWS_REGION_NAME = os.environ.get("AWS_REGION_NAME", "us-west-2")

# 全域 SNS Client（Warm Start 重用）
_sns_client = None


def get_sns_client():
    """取得 SNS Client 實例。"""
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns", region_name=AWS_REGION_NAME)
    return _sns_client


def build_digest_email(elder_name: str, elder_id: str, today_str: str,
                       summary: Dict[str, Any], routines_result: Dict[str, Any]) -> str:
    """組裝每日健康晚報 Email 內文。"""

    # 行程完成狀況
    routine_items = routines_result.get("items", [])
    done_items = [r for r in routine_items if r.get("status") == "done"]
    missed_items = [r for r in routine_items if r.get("status") == "missed"]
    pending_items = [r for r in routine_items if r.get("status") == "pending"]

    total_count = len(routine_items)
    done_count = len(done_items)

    # 完成率彩色圖示
    if total_count == 0:
        completion_icon = "📭"
        completion_text = "今日無安排行程"
    elif done_count == total_count:
        completion_icon = "🎉"
        completion_text = f"今日所有 {total_count} 項行程已全數完成！"
    elif done_count >= total_count * 0.7:
        completion_icon = "✅"
        completion_text = f"今日完成 {done_count}/{total_count} 項行程（{int(done_count/total_count*100)}%）"
    else:
        completion_icon = "⚠️"
        completion_text = f"今日完成 {done_count}/{total_count} 項行程，請留意未完成項目"

    # 逐項行程清單
    routine_lines = []
    for r in done_items:
        routine_lines.append(f"  ✅ {r.get('title', '未知行程')}")
    for r in missed_items:
        routine_lines.append(f"  ❌ {r.get('title', '未知行程')}（已逾期未完成）")
    for r in pending_items:
        routine_lines.append(f"  ⏳ {r.get('title', '未知行程')}（待完成）")

    routines_section = "\n".join(routine_lines) if routine_lines else "  （今日無行程紀錄）"

    # 每日摘要文字
    overview = summary.get("overview", "（今日摘要尚未生成，請稍後登入 App 查看）") if summary else \
               "（今日摘要尚未生成，請稍後登入 App 查看）"

    sections = summary.get("sections", {}) if summary else {}
    section_lines = []
    section_labels = {
        "diet": "🍱 飲食",
        "activity": "🚶 活動",
        "sleep": "😴 睡眠",
        "medication": "💊 用藥",
        "wellbeing": "💛 身心狀況",
        "safety": "🚨 安全",
        "other": "📌 其他",
    }
    for key, label in section_labels.items():
        val = sections.get(key)
        if val:
            section_lines.append(f"  {label}：{val}")

    sections_text = "\n".join(section_lines) if section_lines else "  （詳細分類摘要尚未生成）"

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

    return f"""============================================================
📖 智慧長照 AI 關懷系統 - 每日健康關懷日報
============================================================

長者姓名：{elder_name}
日報日期：{today_str}
發送時間：{now_str} (台灣時間)

------------------------------------------------------------
{completion_icon}【今日例行行程完成狀態】 {completion_text}

{routines_section}

------------------------------------------------------------
📋【今日健康生活總覽】

{overview}

------------------------------------------------------------
🔍【分類健康摘要】

{sections_text}

------------------------------------------------------------
💡 如需查看完整對話紀錄與事件歷史，請登入智慧長照 App。
============================================================"""


def process_elder(elder: Dict[str, Any], today_str: str) -> int:
    """處理單一長者的晚報推播，回傳成功通報的照護者數量。"""
    elder_id = elder.get("elder_id", "")
    elder_name = elder.get("name") or elder.get("nickname") or "長者"
    caregiver_ids = elder.get("caregiver_ids", [])

    if not elder_id or not caregiver_ids:
        logger.info("elder_id=%s 無綁定照護者，跳過晚報", elder_id)
        return 0

    # 取得今日健康摘要
    try:
        summaries, _ = db.list_daily_summaries(elder_id, today_str, today_str)
        summary = summaries[0] if summaries else {}
    except Exception:
        logger.exception("取得摘要失敗：elder_id=%s", elder_id)
        summary = {}

    # 取得今日行程完成狀況
    try:
        routines_result = db.get_daily_routines(elder_id, today_str)
    except Exception:
        logger.exception("取得行程失敗：elder_id=%s", elder_id)
        routines_result = {"items": []}

    # 組裝 Email 內文
    email_body = build_digest_email(elder_name, elder_id, today_str, summary, routines_result)
    subject = f"📖【智慧長照關懷晚報】{elder_name} {today_str} 健康與行程日報"

    # 發送 SNS（一個 Topic 訂閱多個照護者信箱）
    sent_count = 0
    if CAREGIVER_NOTIFY_TOPIC_ARN:
        try:
            sns = get_sns_client()
            sns.publish(
                TopicArn=CAREGIVER_NOTIFY_TOPIC_ARN,
                Subject=subject,
                Message=email_body,
            )
            sent_count = len(caregiver_ids)
            logger.info("晚報已發送：elder_id=%s 照護者數=%s", elder_id, sent_count)
        except Exception:
            logger.exception("SNS 發送晚報失敗：elder_id=%s", elder_id)
    else:
        logger.info("[Mock] 晚報未發送（無 SNS Topic）：elder_id=%s", elder_id)
        sent_count = len(caregiver_ids)

    return sent_count


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """每日晚報 Lambda 主進入點（由 EventBridge Scheduler 觸發）。"""
    logger.info("觸發事件：%s", json.dumps(event, ensure_ascii=False))

    now_taipei = datetime.now(TZ_TAIPEI)
    today_str = now_taipei.strftime("%Y-%m-%d")
    logger.info("處理日期：%s", today_str)

    # 掃描所有長者
    try:
        all_elders: List[Dict[str, Any]] = db.list_elders()
    except Exception:
        logger.exception("掃描 elders 表失敗")
        return {"statusCode": 500, "body": "掃描長者資料失敗"}

    if not all_elders:
        logger.info("目前無任何長者資料，結束晚報")
        return {"statusCode": 200, "body": "無長者資料，晚報作業完成"}

    total_elders = len(all_elders)
    total_sent = 0
    failed = []

    for elder in all_elders:
        elder_id = elder.get("elder_id", "unknown")
        try:
            sent = process_elder(elder, today_str)
            total_sent += sent
        except Exception:
            logger.exception("處理晚報失敗：elder_id=%s", elder_id)
            failed.append(elder_id)

    result_summary = {
        "date": today_str,
        "total_elders": total_elders,
        "total_notifications_sent": total_sent,
        "failed_elder_ids": failed,
    }

    logger.info("晚報作業完成：%s", json.dumps(result_summary, ensure_ascii=False))
    return {
        "statusCode": 200,
        "body": json.dumps(result_summary, ensure_ascii=False),
    }
