"""DynamoDB 存取層（docs/framework.md 與 docs/api.md 資料模型）。

提供 6 張表的統一讀寫介面：
- elders: 長者 persona 與綁定資料
- conversations: 對話紀錄
- events: 結構化生活事件（「實際發生」的唯一紀錄，含例行公事完成）
- daily_summaries: AI 每日摘要
- routines: 例行公事計畫與當日動態行程計算

技術重點：
- 全域連線重用 (Warm Start)
- Decimal 自動遞迴轉碼 (轉換為原生 int/float)
- Base64 分頁游標 (next_token)
- transact_write_items 跨表寫入交易 (Events + Routines 完成狀態)
- 基於「最晚完成時間 / 寬限期」之例行公事動態狀態計算
"""

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from typing import Any
import uuid

import boto3
from botocore.exceptions import ClientError

# 台灣時區 (+08:00)
TZ_TAIPEI = timezone(timedelta(hours=8))

# 環境變數自訂資料表名稱
TABLE_ELDERS = os.environ.get("TABLE_ELDERS", "elders")
TABLE_CONVERSATIONS = os.environ.get("TABLE_CONVERSATIONS", "conversations")
TABLE_EVENTS = os.environ.get("TABLE_EVENTS", "events")
TABLE_DAILY_SUMMARIES = os.environ.get("TABLE_DAILY_SUMMARIES", "daily_summaries")
TABLE_ROUTINES = os.environ.get("TABLE_ROUTINES", "routines")

# 全域 Boto3 資源初始化（連線重用）
_dynamodb = None
_client = None


def get_dynamodb_resource():
    """取得或初始化 DynamoDB Resource 實例。"""
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def get_dynamodb_client():
    """取得或初始化 DynamoDB Client 實例（用於 transact_write_items）。"""
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


class DBError(Exception):
    """資料庫操作基本例外。"""
    pass


class ItemNotFoundError(DBError):
    """指定之資料項目不存在。"""
    pass


# -----------------------------------------------------------------------------
# 輔助函式：Decimal 轉換與分頁游標
# -----------------------------------------------------------------------------

def convert_decimals(obj: Any) -> Any:
    """遞迴將 DynamoDB 回傳之 Decimal 物件轉換為 Python 原生 int 或 float。"""
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    return obj


def encode_next_token(key: dict[str, Any]) -> str:
    """將 LastEvaluatedKey 轉碼為 Base64 不透明 next_token。"""
    json_bytes = json.dumps(convert_decimals(key)).encode("utf-8")
    return base64.b64encode(json_bytes).decode("utf-8")


def decode_next_token(token: str) -> dict[str, Any]:
    """將 Base64 next_token 解碼為 DynamoDB ExclusiveStartKey。"""
    try:
        json_bytes = base64.b64decode(token.encode("utf-8"))
        return json.loads(json_bytes.decode("utf-8"))
    except Exception as e:
        raise DBError(f"無效的分頁游標 next_token: {e}")


# -----------------------------------------------------------------------------
# Elders 表操作
# -----------------------------------------------------------------------------

def get_elder(elder_id: str) -> dict[str, Any] | None:
    """取得單一長者資料。"""
    table = get_dynamodb_resource().Table(TABLE_ELDERS)
    try:
        resp = table.get_item(Key={"elder_id": elder_id})
        item = resp.get("Item")
        return convert_decimals(item) if item else None
    except ClientError as e:
        raise DBError(f"讀取長者資料失敗: {e.response['Error']['Message']}")


def create_elder(elder_data: dict[str, Any]) -> dict[str, Any]:
    """新增長者資料。自動補充 elder_id（若無）、created_at 與 updated_at 時間戳記。"""
    table = get_dynamodb_resource().Table(TABLE_ELDERS)
    
    # 複製資料避免改動原始帶入字典
    data = dict(elder_data)
    
    # 自動補全 elder_id (前綴 eld_)
    if not data.get("elder_id"):
        data["elder_id"] = f"eld_{uuid.uuid4().hex[:12]}"
        
    # 自動補全 ISO 8601 台灣時間戳記 (+08:00)
    now_str = datetime.now(TZ_TAIPEI).isoformat()
    if not data.get("created_at"):
        data["created_at"] = now_str
    if not data.get("updated_at"):
        data["updated_at"] = now_str
        
    # 預設 List 欄位
    for list_key in ("health_notes", "family", "caregiver_ids"):
        if data.get(list_key) is None:
            data[list_key] = []
            
    try:
        table.put_item(Item=data)
        return convert_decimals(data)
    except ClientError as e:
        raise DBError(f"建立長者資料失敗: {e.response['Error']['Message']}")


def update_elder(elder_id: str, patch_data: dict[str, Any]) -> dict[str, Any]:
    """更新長者資料（部分更新）。自動刷新 updated_at 時間戳記。"""
    table = get_dynamodb_resource().Table(TABLE_ELDERS)

    # 複製 patch_data 並注入/更新 updated_at
    data = dict(patch_data)
    data["updated_at"] = datetime.now(TZ_TAIPEI).isoformat()

    # 動態建構 UpdateExpression
    update_parts = []
    expr_names = {}
    expr_values = {}
    for k, v in data.items():
        if k in ("elder_id", "created_at"):
            continue
        attr_key = f"#{k}"
        attr_val = f":{k}"
        update_parts.append(f"{attr_key} = {attr_val}")
        expr_names[attr_key] = k
        expr_values[attr_val] = v

    if not update_parts:
        return get_elder(elder_id) or {}

    update_expr = "SET " + ", ".join(update_parts)
    try:
        resp = table.update_item(
            Key={"elder_id": elder_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
        return convert_decimals(resp.get("Attributes", {}))
    except ClientError as e:
        raise DBError(f"更新長者資料失敗: {e.response['Error']['Message']}")


def list_elders(caregiver_id: str = None) -> list[dict[str, Any]]:
    """列表查詢長者。若帶入 caregiver_id 則進行關聯過濾。"""
    table = get_dynamodb_resource().Table(TABLE_ELDERS)
    try:
        if caregiver_id:
            resp = table.scan(
                FilterExpression="contains(caregiver_ids, :cid)",
                ExpressionAttributeValues={":cid": caregiver_id},
            )
        else:
            resp = table.scan()
        items = resp.get("Items", [])
        return convert_decimals(items)
    except ClientError as e:
        raise DBError(f"查詢長者列表失敗: {e.response['Error']['Message']}")


# -----------------------------------------------------------------------------
# Conversations 表操作
# -----------------------------------------------------------------------------

def save_conversation(conversation_data: dict[str, Any]) -> dict[str, Any]:
    """儲存對話紀錄。自動補充 conversation_id（若無）與 created_at 時間戳記。"""
    table = get_dynamodb_resource().Table(TABLE_CONVERSATIONS)
    
    data = dict(conversation_data)
    
    # 自動補全 conversation_id (前綴 cnv_)
    if not data.get("conversation_id"):
        data["conversation_id"] = f"cnv_{uuid.uuid4().hex[:12]}"
        
    # 自動補全 created_at 時間戳記 (+08:00)
    if not data.get("created_at"):
        data["created_at"] = datetime.now(TZ_TAIPEI).isoformat()
        
    # 設定預設欄位
    data.setdefault("source", "elder_initiated")
    data.setdefault("user_status", "replied")
    data.setdefault("system_status", "success")
    data.setdefault("lang", "zh-TW")
    data.setdefault("input_type", "text")
    data.setdefault("routines_updated", False)

    try:
        table.put_item(Item=data)
        return convert_decimals(data)
    except ClientError as e:
        raise DBError(f"儲存對話紀錄失敗: {e.response['Error']['Message']}")


def get_recent_conversations(
    elder_id: str, limit: int = 10, next_token: str = None
) -> tuple[list[dict[str, Any]], str | None]:
    """分頁查詢長者近期對話紀錄（按 created_at 時間倒序）。"""
    table = get_dynamodb_resource().Table(TABLE_CONVERSATIONS)
    
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "elder_id = :eid",
        "ExpressionAttributeValues": {":eid": elder_id},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = table.query(**query_kwargs)
        items = convert_decimals(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        new_next_token = encode_next_token(last_key) if last_key else None
        return items, new_next_token
    except ClientError as e:
        raise DBError(f"查詢對話紀錄失敗: {e.response['Error']['Message']}")


# -----------------------------------------------------------------------------
# Events 表操作
# -----------------------------------------------------------------------------

def create_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """新增生活事件。支援依據 canonical_event_key 自動計算穩定 event_id 與產生 event_time_key。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    data = dict(event_data)
    
    elder_id = data.get("elder_id", "")
    canonical_key = data.get("canonical_event_key")
    
    if not data.get("event_id"):
        if canonical_key:
            stable_sig = f"{elder_id}:{canonical_key}".encode("utf-8")
            data["event_id"] = f"evt_{hashlib.sha256(stable_sig).hexdigest()[:12]}"
        else:
            data["event_id"] = f"evt_{uuid.uuid4().hex[:12]}"
            
    ts = data.get("ts") or datetime.now(TZ_TAIPEI).isoformat()
    data["ts"] = ts
    data["event_time_key"] = f"{ts}#{data['event_id']}"
    data.setdefault("revision", 1)
    data.setdefault("schema_version", 1)
    data.setdefault("evidence_conversation_ids", [])
    data.setdefault("source", "conversation")
    data.setdefault("extraction_track", "batch")

    try:
        table.put_item(Item=data)
        return convert_decimals(data)
    except ClientError as e:
        raise DBError(f"建立事件紀錄失敗: {e.response['Error']['Message']}")


def list_events(
    elder_id: str,
    from_date: str = None,
    to_date: str = None,
    event_type: str = None,
    limit: int = 50,
    next_token: str = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """分頁查詢生活事件。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)

    key_cond = "elder_id = :eid"
    expr_values: dict[str, Any] = {":eid": elder_id}
    expr_names: dict[str, str] = {}

    if from_date and to_date:
        start_ts = f"{from_date}T00:00:00+08:00"
        end_ts = f"{to_date}T23:59:59+08:00"
        key_cond += " AND ts BETWEEN :start_ts AND :end_ts"
        expr_values[":start_ts"] = start_ts
        expr_values[":end_ts"] = end_ts

    filter_exprs = []
    if event_type:
        filter_exprs.append("#t = :etype")
        expr_names["#t"] = "type"
        expr_values[":etype"] = event_type

    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": key_cond,
        "ExpressionAttributeValues": expr_values,
        "ScanIndexForward": False,
        "Limit": limit,
    }

    if filter_exprs:
        query_kwargs["FilterExpression"] = " AND ".join(filter_exprs)
    if expr_names:
        query_kwargs["ExpressionAttributeNames"] = expr_names
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = table.query(**query_kwargs)
        items = convert_decimals(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        new_next_token = encode_next_token(last_key) if last_key else None
        return items, new_next_token
    except ClientError as e:
        raise DBError(f"查詢生活事件失敗: {e.response['Error']['Message']}")


# -----------------------------------------------------------------------------
# Routines 表操作與動態行程比對
# -----------------------------------------------------------------------------

def create_routine(routine_data: dict[str, Any]) -> dict[str, Any]:
    """新增例行公事定義。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    try:
        table.put_item(Item=routine_data)
        return convert_decimals(routine_data)
    except ClientError as e:
        raise DBError(f"建立例行公事失敗: {e.response['Error']['Message']}")


def update_routine(routine_id: str, patch_data: dict[str, Any]) -> dict[str, Any]:
    """更新/停用例行公事。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)

    update_parts = []
    expr_names = {}
    expr_values = {}
    for k, v in patch_data.items():
        if k in ("routine_id", "elder_id", "created_at"):
            continue
        attr_key = f"#{k}"
        attr_val = f":{k}"
        update_parts.append(f"{attr_key} = {attr_val}")
        expr_names[attr_key] = k
        expr_values[attr_val] = v

    if not update_parts:
        resp = table.get_item(Key={"routine_id": routine_id})
        return convert_decimals(resp.get("Item", {}))

    update_expr = "SET " + ", ".join(update_parts)
    try:
        resp = table.update_item(
            Key={"routine_id": routine_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
        return convert_decimals(resp.get("Attributes", {}))
    except ClientError as e:
        raise DBError(f"更新例行公事失敗: {e.response['Error']['Message']}")


def list_routines_by_elder(elder_id: str, active_only: bool = True) -> list[dict[str, Any]]:
    """列表查詢長者的例行公事定義。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    try:
        resp = table.scan(
            FilterExpression="elder_id = :eid",
            ExpressionAttributeValues={":eid": elder_id},
        )
        items = resp.get("Items", [])
        if active_only:
            items = [i for i in items if i.get("active", True)]
        return convert_decimals(items)
    except ClientError as e:
        raise DBError(f"查詢例行公事定義失敗: {e.response['Error']['Message']}")


def get_daily_routines(
    elder_id: str,
    date_str: str,
    current_iso_ts: str = None,
    grace_period_hours: float = 2.0,
) -> dict[str, Any]:
    """展開指定日期的例行公事行程與動態完成狀態 (pending/done/missed)。

    判斷規則：
    1. 從 routines 讀取所有活躍項目，判斷該日期是否需排程 (daily / weekly / once)。
    2. 從 events 查詢該日所有完成記錄 (帶有 routine_id 的事件)。
    3. 若有對應完成事件 => status = 'done'。
    4. 若無完成事件 =>
       - 比對目前時間 (current_iso_ts) 與排定時間 (scheduled_at) + 寬限期 (grace_period_hours)。
       - 超過排定時間 + 寬限期或超過當日最晚完成時間 => status = 'missed'。
       - 否則 => status = 'pending'。
    """
    # 解析日期與目前時間
    dt_target = datetime.strptime(date_str, "%Y-%m-%d")
    target_weekday = dt_target.isoweekday()  # 1-7 (週一為 1)

    if current_iso_ts:
        now_dt = datetime.fromisoformat(current_iso_ts)
    else:
        now_dt = datetime.now(TZ_TAIPEI)

    # 1. 讀取 routines
    routines = list_routines_by_elder(elder_id, active_only=True)

    # 2. 讀取當日 events
    events, _ = list_events(elder_id, from_date=date_str, to_date=date_str, limit=200)
    completed_event_map = {}
    for evt in events:
        rid = evt.get("routine_id")
        if rid:
            completed_event_map[rid] = evt

    daily_items = []

    for rtn in routines:
        schedule = rtn.get("schedule", {})
        freq = schedule.get("freq")
        is_scheduled = False

        if freq == "daily":
            is_scheduled = True
        elif freq == "weekly":
            weekday = schedule.get("weekday")
            if weekday == target_weekday:
                is_scheduled = True
        elif freq == "once":
            if schedule.get("date") == date_str:
                is_scheduled = True

        if not is_scheduled:
            continue

        rid = rtn["routine_id"]
        time_str = schedule.get("time", "09:00")
        scheduled_at_str = f"{date_str}T{time_str}:00+08:00"
        scheduled_dt = datetime.fromisoformat(scheduled_at_str)

        # 動態判定狀態
        if rid in completed_event_map:
            evt = completed_event_map[rid]
            status = "done"
            completed_at = evt.get("ts")
            completed_by = evt.get("source", "conversation")
        else:
            completed_at = None
            completed_by = None
            # 檢查寬限期與截止時間
            deadline_dt = scheduled_dt + timedelta(hours=grace_period_hours)

            # 若排定時間加上寬限期已過，或是目標日期為過去日期
            if now_dt > deadline_dt or now_dt.date() > dt_target.date():
                status = "missed"
            else:
                status = "pending"

        item = {
            "routine_id": rid,
            "title": rtn.get("title", ""),
            "type": rtn.get("type", "other"),
            "scheduled_at": scheduled_at_str,
            "status": status,
        }
        if completed_at:
            item["completed_at"] = completed_at
        if completed_by:
            item["completed_by"] = completed_by

        daily_items.append(item)

    # 排序：依 scheduled_at
    daily_items.sort(key=lambda x: x["scheduled_at"])

    return {"date": date_str, "items": daily_items}


# -----------------------------------------------------------------------------
# TransactWriteItems 跨表連動交易操作
# -----------------------------------------------------------------------------

def complete_routine_with_event(
    elder_id: str,
    routine_id: str,
    date_str: str,
    event_id: str,
    ts: str,
    source: str = "manual",
    detail: str = "手動確認完成例行公事",
    event_type: str = "other",
) -> dict[str, Any]:
    """使用 transact_write_items 連動寫入事件與更新行程完成狀態（確保原子性）。"""
    client = get_dynamodb_client()

    event_item = {
        "event_id": {"S": event_id},
        "elder_id": {"S": elder_id},
        "ts": {"S": ts},
        "type": {"S": event_type},
        "detail": {"S": detail},
        "source": {"S": source},
        "routine_id": {"S": routine_id},
    }

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": TABLE_EVENTS,
                        "Item": event_item,
                    }
                }
            ]
        )
        return {
            "routine_id": routine_id,
            "status": "done",
            "completed_at": ts,
            "completed_by": source,
        }
    except ClientError as e:
        raise DBError(f"連動交易寫入失敗: {e.response['Error']['Message']}")


# -----------------------------------------------------------------------------
# Daily Summaries 表操作
# -----------------------------------------------------------------------------

def save_daily_summary(summary_data: dict[str, Any]) -> dict[str, Any]:
    """儲存/覆寫每日摘要。"""
    table = get_dynamodb_resource().Table(TABLE_DAILY_SUMMARIES)
    try:
        table.put_item(Item=summary_data)
        return convert_decimals(summary_data)
    except ClientError as e:
        raise DBError(f"儲存每日摘要失敗: {e.response['Error']['Message']}")


def get_daily_summaries(elder_id: str, from_date: str, to_date: str) -> list[dict[str, Any]]:
    """範圍查詢每日摘要。"""
    table = get_dynamodb_resource().Table(TABLE_DAILY_SUMMARIES)
    try:
        resp = table.query(
            KeyConditionExpression="elder_id = :eid AND #d BETWEEN :from_d AND :to_d",
            ExpressionAttributeNames={"#d": "date"},
            ExpressionAttributeValues={
                ":eid": elder_id,
                ":from_d": from_date,
                ":to_d": to_date,
            },
            ScanIndexForward=True,
        )
        return convert_decimals(resp.get("Items", []))
    except ClientError as e:
        raise DBError(f"查詢每日摘要失敗: {e.response['Error']['Message']}")

# -----------------------------------------------------------------------------
# Memories 表操作
# -----------------------------------------------------------------------------

def get_memories(elder_id: str) -> list[dict[str, Any]]:
    """查詢長者長期記憶。"""
    table = get_dynamodb_resource().Table(TABLE_MEMORIES)
    try:
        resp = table.query(
            KeyConditionExpression="elder_id = :eid",
            ExpressionAttributeValues={":eid": elder_id},
        )
        return convert_decimals(resp.get("Items", []))
    except ClientError as e:
        raise DBError(f"查詢長期記憶失敗: {e.response['Error']['Message']}")


def save_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """寫入/更新長期記憶。"""
    table = get_dynamodb_resource().Table(TABLE_MEMORIES)
    try:
        table.put_item(Item=memory_data)
        return convert_decimals(memory_data)
    except ClientError as e:
        raise DBError(f"儲存長期記憶失敗: {e.response['Error']['Message']}")
