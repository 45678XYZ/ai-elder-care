"""DynamoDB 存取層（docs/framework.md 與 docs/api.md 資料模型）。

提供 5 張表的統一讀寫介面：
- elders: 長者 persona 與綁定資料
- conversations: 對話紀錄
- events: 結構化生活事件（「實際發生」的唯一紀錄，含例行公事完成）
- daily_summaries: AI 每日摘要
- routines: 例行公事計畫與當日動態行程計算

技術重點：
- 全域連線重用 (Warm Start)
- Decimal 自動遞迴轉碼 (轉換為原生 int/float)
- Base64 分頁游標 (next_token)
- 條件式寫入與 transact_write_items（canonical event 冪等、routine 版本推進）
- routine 的 occurrence 狀態不落地，推導邏輯見 src/shared/routines.py
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
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

# 台灣時區 (+08:00)
TZ_TAIPEI = timezone(timedelta(hours=8))

# 環境變數自訂資料表名稱
TABLE_ELDERS = os.environ.get("TABLE_ELDERS", "elders")
TABLE_CONVERSATIONS = os.environ.get("TABLE_CONVERSATIONS", "conversations")
TABLE_EVENTS = os.environ.get("TABLE_EVENTS", "events")
TABLE_DAILY_SUMMARIES = os.environ.get("TABLE_DAILY_SUMMARIES", "daily_summaries")
TABLE_ROUTINES = os.environ.get("TABLE_ROUTINES", "routines")

# BatchGetItem 遇節流時重取未處理 key 的次數上限
BATCH_GET_MAX_ATTEMPTS = 3

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


class ConditionFailedError(DBError):
    """條件式寫入未通過：項目已存在，或狀態已被其他請求推進。"""
    pass


# -----------------------------------------------------------------------------
# 輔助函式：Decimal 轉換與分頁游標
# -----------------------------------------------------------------------------

_serializer = TypeSerializer()


def to_attribute_values(data: dict[str, Any]) -> dict[str, Any]:
    """Python 值轉為 client 層 API（transact_write_items）所需的 AttributeValue。"""
    return {k: _serializer.serialize(v) for k, v in data.items()}


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

def event_id_for(elder_id: str, canonical_event_key: str) -> str:
    """由 elder_id + canonical key 穩定產生 event_id：同一 canonical 事件永遠同一個 ID。"""
    stable_sig = f"{elder_id}:{canonical_event_key}".encode("utf-8")
    return f"evt_{hashlib.sha256(stable_sig).hexdigest()[:12]}"


def _prepare_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """補齊 event_id、event_time_key 與預設欄位。"""
    data = dict(event_data)

    if not data.get("event_id"):
        canonical_key = data.get("canonical_event_key")
        if canonical_key:
            data["event_id"] = event_id_for(data.get("elder_id", ""), canonical_key)
        else:
            data["event_id"] = f"evt_{uuid.uuid4().hex[:12]}"

    ts = data.get("ts") or datetime.now(TZ_TAIPEI).isoformat(timespec="milliseconds")
    data["ts"] = ts
    data["event_time_key"] = f"{ts}#{data['event_id']}"
    data.setdefault("revision", 1)
    data.setdefault("schema_version", 1)
    data.setdefault("evidence_conversation_ids", [])
    data.setdefault("source", "conversation")
    data.setdefault("extraction_track", "batch")
    return data


def create_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """新增生活事件。支援依據 canonical_event_key 自動計算穩定 event_id 與產生 event_time_key。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    data = _prepare_event(event_data)

    try:
        table.put_item(Item=data)
        return convert_decimals(data)
    except ClientError as e:
        raise DBError(f"建立事件紀錄失敗: {e.response['Error']['Message']}")


def put_event_if_absent(event_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """條件式寫入事件，回傳 (事件, 是否為本次建立)。

    同一 canonical event 已存在時不覆寫既有事實（例如對話已完成的 routine 又被手動確認），
    改回既有項目達成冪等。
    """
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    data = _prepare_event(event_data)

    try:
        table.put_item(Item=data, ConditionExpression="attribute_not_exists(event_id)")
        return convert_decimals(data), True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise DBError(f"建立事件紀錄失敗: {e.response['Error']['Message']}")

    existing = get_event(data["elder_id"], data["event_id"])
    if existing is None:
        raise DBError("事件已存在但讀取失敗")
    return existing, False


def get_event(elder_id: str, event_id: str) -> dict[str, Any] | None:
    """強一致取得單一事件。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        resp = table.get_item(Key={"elder_id": elder_id, "event_id": event_id}, ConsistentRead=True)
        item = resp.get("Item")
        return convert_decimals(item) if item else None
    except ClientError as e:
        raise DBError(f"讀取事件紀錄失敗: {e.response['Error']['Message']}")


def get_events(elder_id: str, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    """依 event_id 批次取事件，回傳 {event_id: 事件}；不存在者不出現在結果中。"""
    table_name = TABLE_EVENTS
    resource = get_dynamodb_resource()
    found: dict[str, dict[str, Any]] = {}

    # BatchGetItem 單次上限 100 筆；被節流的 key 有限次重取，避免 Lambda 卡到逾時
    for start in range(0, len(event_ids), 100):
        keys = [{"elder_id": elder_id, "event_id": eid} for eid in event_ids[start:start + 100]]
        for _ in range(BATCH_GET_MAX_ATTEMPTS):
            if not keys:
                break
            try:
                resp = resource.batch_get_item(
                    RequestItems={table_name: {"Keys": keys, "ConsistentRead": True}}
                )
            except ClientError as e:
                raise DBError(f"批次讀取事件失敗: {e.response['Error']['Message']}")
            for item in resp.get("Responses", {}).get(table_name, []):
                found[item["event_id"]] = convert_decimals(item)
            keys = resp.get("UnprocessedKeys", {}).get(table_name, {}).get("Keys", [])
        if keys:
            raise DBError("批次讀取事件失敗: 仍有未處理的 key")

    return found


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
# Routines 表操作（不可變版本：PK routine_id + SK version）
# -----------------------------------------------------------------------------

def list_routine_versions(routine_id: str) -> list[dict[str, Any]]:
    """強一致取得單一 routine 的所有版本，依 version 遞增；最後一筆即 current 版本。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    items: list[dict[str, Any]] = []
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "routine_id = :rid",
        "ExpressionAttributeValues": {":rid": routine_id},
        "ConsistentRead": True,
    }

    try:
        while True:
            resp = table.query(**query_kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
    except ClientError as e:
        raise DBError(f"查詢例行公事版本失敗: {e.response['Error']['Message']}")

    return convert_decimals(items)


def get_routine_version(routine_id: str, version: int) -> dict[str, Any] | None:
    """強一致取得指定版本。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    try:
        resp = table.get_item(
            Key={"routine_id": routine_id, "version": version}, ConsistentRead=True
        )
        item = resp.get("Item")
        return convert_decimals(item) if item else None
    except ClientError as e:
        raise DBError(f"讀取例行公事版本失敗: {e.response['Error']['Message']}")


def put_routine_version(item: dict[str, Any]) -> dict[str, Any]:
    """條件式建立 routine 版本。

    版本不可變，因此同一 (routine_id, version) 已存在時不覆寫而拋 ConditionFailedError，
    由呼叫端比對 request_hash 判斷是重送（回既有結果）或衝突。
    """
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(routine_id)")
        return convert_decimals(item)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConditionFailedError("例行公事版本已存在")
        raise DBError(f"建立例行公事失敗: {e.response['Error']['Message']}")


def replace_current_routine_version(
    current: dict[str, Any], next_version: dict[str, Any]
) -> dict[str, Any]:
    """以單一 transaction 關閉舊 current 版並寫入下一版。

    舊版仍為 current 且新版尚未存在才成立；條件不成立代表有並行修改，拋
    ConditionFailedError 讓呼叫端重新判斷是重送或衝突。
    """
    client = get_dynamodb_client()
    effective_to = next_version["effective_from"]

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": TABLE_ROUTINES,
                        "Key": to_attribute_values(
                            {
                                "routine_id": current["routine_id"],
                                "version": int(current["version"]),
                            }
                        ),
                        "UpdateExpression": (
                            "SET is_current = :false, effective_to = :ts REMOVE current_sort_key"
                        ),
                        "ConditionExpression": "is_current = :true",
                        "ExpressionAttributeValues": to_attribute_values(
                            {":false": False, ":true": True, ":ts": effective_to}
                        ),
                    }
                },
                {
                    "Put": {
                        "TableName": TABLE_ROUTINES,
                        "Item": to_attribute_values(next_version),
                        "ConditionExpression": "attribute_not_exists(routine_id)",
                    }
                },
            ]
        )
        return convert_decimals(next_version)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ConditionFailedError("例行公事已被其他請求改版")
        raise DBError(f"更新例行公事失敗: {e.response['Error']['Message']}")


def list_current_routines(
    elder_id: str,
    active_only: bool = True,
    limit: int = 50,
    next_token: str = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """查 sparse GSI `routines-current-by-elder` 取得長者的 current 定義（最終一致）。"""
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)

    key_cond = "elder_id = :eid"
    expr_values: dict[str, Any] = {":eid": elder_id}
    if active_only:
        key_cond += " AND begins_with(current_sort_key, :prefix)"
        expr_values[":prefix"] = "A#"

    query_kwargs: dict[str, Any] = {
        "IndexName": "routines-current-by-elder",
        "KeyConditionExpression": key_cond,
        "ExpressionAttributeValues": expr_values,
        "Limit": limit,
    }
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = table.query(**query_kwargs)
        items = convert_decimals(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        return items, encode_next_token(last_key) if last_key else None
    except ClientError as e:
        raise DBError(f"查詢例行公事定義失敗: {e.response['Error']['Message']}")


def list_routine_versions_by_elder(elder_id: str, upper_bound: str) -> list[dict[str, Any]]:
    """查 GSI `routine-versions-by-elder` 取得 effective_from 不晚於 upper_bound 的所有版本。

    upper_bound 為 `version_time_key` 的上界（見 src/shared/routines.py 的
    versions_upper_bound），供展開指定日期行程時收斂各 routine 的有效版本。
    """
    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    items: list[dict[str, Any]] = []
    query_kwargs: dict[str, Any] = {
        "IndexName": "routine-versions-by-elder",
        "KeyConditionExpression": "elder_id = :eid AND version_time_key <= :upper",
        "ExpressionAttributeValues": {":eid": elder_id, ":upper": upper_bound},
    }

    try:
        while True:
            resp = table.query(**query_kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
    except ClientError as e:
        raise DBError(f"查詢例行公事版本失敗: {e.response['Error']['Message']}")

    return convert_decimals(items)


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

