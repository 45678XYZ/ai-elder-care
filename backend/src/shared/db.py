"""DynamoDB 存取層（docs/framework.md 與 docs/api.md 資料模型）。

提供 5 張表的統一讀寫介面：
- elders: 長者 persona 與綁定資料
- conversations: 對話紀錄
- events: 結構化生活事件（「實際發生」的唯一紀錄，含例行公事完成）
- daily_summaries: AI 每日摘要
- routines: 例行公事計畫與當日動態行程計算

技術重點：
- 全域連線重用 (Warm Start)
- Decimal 自動遞迴轉碼 (讀出轉原生 int/float、寫入轉 Decimal 並剔除 None)
- Base64 分頁游標 (next_token)
- events 一律條件式寫入：canonical key 決定身分，內容互斥不靜默覆寫，
  safety enrichment 以 revision 為條件遞增（規則見 docs/framework.md）
- 事件時間軸走 GSI events-by-time，日界以台灣時間計算
- 基於「最晚完成時間 / 寬限期」之例行公事動態狀態計算
"""

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from typing import Any
import uuid

import boto3
from botocore.exceptions import ClientError

# canonical 事件身分與時間正規化只有一份實作，避免同一筆資料在兩處算出不同 ID／精度
from src.extraction.canonical import event_id_for, event_time_key, routine_completion_key
from src.extraction.temporal import day_end, day_start, format_ts, normalize_ts

logger = logging.getLogger(__name__)

# 台灣時區 (+08:00)
TZ_TAIPEI = timezone(timedelta(hours=8))

# 環境變數自訂資料表名稱
TABLE_ELDERS = os.environ.get("TABLE_ELDERS", "elders")
TABLE_CONVERSATIONS = os.environ.get("TABLE_CONVERSATIONS", "conversations")
TABLE_EVENTS = os.environ.get("TABLE_EVENTS", "events")
TABLE_DAILY_SUMMARIES = os.environ.get("TABLE_DAILY_SUMMARIES", "daily_summaries")
TABLE_ROUTINES = os.environ.get("TABLE_ROUTINES", "routines")

# 事件時間軸索引；events 的 Base table SK 是 event_id，時間範圍查詢一律走此 GSI
EVENTS_BY_TIME_INDEX = "events-by-time"

# 對話時間軸索引；摘要要算當日 turn 數與相關 session，只能走此 GSI
CONVERSATIONS_BY_TIME_INDEX = "conversations-by-time"

# 時間軸 sort key 都是 `<ts>#<id>`；查詢上界要蓋過同一毫秒的所有 id
TIME_KEY_UPPER_BOUND_SUFFIX = "#\uffff"

# 舊名保留：events 相關程式與測試沿用
EVENT_TIME_KEY_UPPER_BOUND_SUFFIX = TIME_KEY_UPPER_BOUND_SUFFIX

# 判斷「既有事件與這次寫入是否互斥」時比對的事實欄位。
# 刻意不含 `ts`：canonical key 已經固定了日期與 Slot，同一件事在 Slot 內被再次提到、
# 或 routine 完成先後由兩個入口寫入時，時間本來就會不同，那不是矛盾而是「先寫者為準」。
EVENT_MATERIAL_FIELDS: tuple[str, ...] = (
    "canonical_event_key",
    "type",
    "detail",
    "concept_id",
    "routine_id",
    "routine_date",
)

# 合法 enrichment 可更新的欄位；事件身分與既有事實欄位不得覆寫
ENRICHABLE_EVENT_FIELDS: frozenset[str] = frozenset(
    {"detail", "structured_detail", "evidence_conversation_ids", "confidence"}
)

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


class EventConflictError(DBError):
    """同一 canonical event 已存在且內容互斥；既有資料保留不動，工作應失敗並告警。"""

    def __init__(self, event_id: str, canonical_event_key: str, differences: dict[str, Any]):
        super().__init__(f"事件內容互斥（{event_id}）：{sorted(differences)}")
        self.event_id = event_id
        self.canonical_event_key = canonical_event_key
        self.differences = differences


class EventRevisionConflictError(DBError):
    """enrichment 的 revision 條件不成立；代表其他 worker 已先更新，應重讀後重試。"""

    def __init__(self, event_id: str, expected_revision: int):
        super().__init__(f"事件 revision 條件不成立（{event_id}，預期 {expected_revision}）")
        self.event_id = event_id
        self.expected_revision = expected_revision


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


def prepare_item(obj: Any) -> Any:
    """把 Python 物件轉成 DynamoDB 可寫入的形式。

    兩件事都是必要的，不是潔癖：boto3 resource 不接受 `float`（萃取出的血壓、信心值都是
    float），而動態 schema 會把所有祖先屬性攤平、未提及即為 `None`，不剔除的話 item 會
    塞滿空欄位並逼近 400 KB 上限。
    """
    if isinstance(obj, float):
        # 走字串轉換避免二進位浮點誤差被寫進資料
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [prepare_item(item) for item in obj if item is not None]
    if isinstance(obj, dict):
        return {key: prepare_item(value) for key, value in obj.items() if value is not None}
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


def list_turns_by_day(elder_id: str, date: str, *, page_size: int = 100) -> list[dict[str, Any]]:
    """取某一天（台灣日界）的所有 turn，依時間正序。

    走 GSI `conversations-by-time`：Base table 的 SK 是 `record_id`，用時間當範圍條件在
    Base table 上不成立。上界補 `#\uffff` 讓落在 `23:59:59.999` 的 turn 不被排除。

    一天的 turn 數有 session 上限撐著（每 session ≤ 100），因此這裡把分頁跑完再回傳，
    呼叫端不必自己迴圈；摘要需要的是「當天全部」而不是一頁。
    """
    query_kwargs: dict[str, Any] = {
        "IndexName": CONVERSATIONS_BY_TIME_INDEX,
        "KeyConditionExpression": (
            "elder_id = :eid AND conversation_time_key BETWEEN :start_key AND :end_key"
        ),
        "ExpressionAttributeValues": {
            ":eid": elder_id,
            ":start_key": day_start(date),
            ":end_key": day_end(date) + TIME_KEY_UPPER_BOUND_SUFFIX,
        },
        "ScanIndexForward": True,
        "Limit": page_size,
    }

    table = get_dynamodb_resource().Table(TABLE_CONVERSATIONS)
    turns: list[dict[str, Any]] = []
    while True:
        try:
            resp = table.query(**query_kwargs)
        except ClientError as e:
            raise DBError(f"查詢當日對話失敗: {e.response['Error']['Message']}")
        turns.extend(convert_decimals(resp.get("Items", [])))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return turns
        query_kwargs["ExclusiveStartKey"] = last_key


# -----------------------------------------------------------------------------
# Events 表操作
# -----------------------------------------------------------------------------

def get_event(elder_id: str, event_id: str) -> dict[str, Any] | None:
    """讀取單一事件；不存在回 None。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        resp = table.get_item(Key={"elder_id": elder_id, "event_id": event_id})
    except ClientError as e:
        raise DBError(f"讀取事件紀錄失敗: {e.response['Error']['Message']}")
    item = resp.get("Item")
    return convert_decimals(item) if item else None


def create_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """以條件式寫入建立事件；命中相同內容視為冪等，內容互斥則拋出衝突。

    寫入規則見 docs/framework.md 的「Canonical identity 與寫入規則」：

    - `canonical_event_key` 與 `ts` 必填。沒有 canonical key 就沒有穩定事件身分，
      退回隨機 ID 會讓 retry 與 duplicate delivery 各寫一筆；`ts` 也不能用當下時間補，
      否則同一段對話重跑會落到不同 Slot。
    - `attribute_not_exists(event_id)` 條件式 Put。已存在時比對事實欄位：
      完全相同 → 冪等回傳既有資料；互斥 → 保留既有、拋 `EventConflictError` 讓工作失敗告警。
    """
    elder_id = event_data.get("elder_id")
    canonical_key = event_data.get("canonical_event_key")
    ts_raw = event_data.get("ts")
    if not elder_id:
        raise DBError("建立事件需要 elder_id")
    if not canonical_key:
        raise DBError("建立事件需要 canonical_event_key（不得以隨機 ID 代替）")
    if not ts_raw:
        raise DBError("建立事件需要 ts（不得以當下時間代替，否則 retry 不冪等）")

    data = dict(event_data)
    data["ts"] = normalize_ts(ts_raw)
    data["event_id"] = data.get("event_id") or event_id_for(elder_id, canonical_key)
    data["event_time_key"] = event_time_key(data["ts"], data["event_id"])
    data.setdefault("revision", 1)
    data.setdefault("schema_version", 1)
    data.setdefault("evidence_conversation_ids", [])
    data.setdefault("source", "conversation")
    data.setdefault("extraction_track", "batch")
    now = format_ts(datetime.now(TZ_TAIPEI))
    data.setdefault("created_at", now)
    data.setdefault("updated_at", data["created_at"])

    item = prepare_item(data)
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(event_id)")
        return convert_decimals(item)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise DBError(f"建立事件紀錄失敗: {e.response['Error']['Message']}")

    existing = get_event(elder_id, data["event_id"]) or {}
    differences = _material_differences(existing, convert_decimals(item))
    if differences:
        logger.error(
            "事件內容互斥，保留既有資料：event_id=%s canonical_event_key=%s differences=%s",
            data["event_id"],
            canonical_key,
            differences,
        )
        raise EventConflictError(
            event_id=data["event_id"],
            canonical_event_key=str(canonical_key),
            differences=differences,
        )
    logger.info("事件已存在且內容相同，視為冪等：event_id=%s", data["event_id"])
    return existing


def enrich_event(
    elder_id: str,
    event_id: str,
    expected_revision: int,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """以目前 `revision` 為條件遞增並補充事件內容。

    唯一可條件更新的情境是既有 realtime safety event 的合法 enrichment；
    事件身分與既有事實欄位不得覆寫，因此只允許 `ENRICHABLE_EVENT_FIELDS` 內的欄位。
    """
    illegal = sorted(set(updates) - ENRICHABLE_EVENT_FIELDS)
    if illegal:
        raise DBError(f"事件 enrichment 不得修改這些欄位：{', '.join(illegal)}")
    if not updates:
        raise DBError("事件 enrichment 需要至少一個欄位")

    set_parts = ["#revision = :next_revision", "#updated_at = :updated_at"]
    expr_names = {"#revision": "revision", "#updated_at": "updated_at"}
    expr_values: dict[str, Any] = {
        ":next_revision": expected_revision + 1,
        ":expected_revision": expected_revision,
        ":updated_at": format_ts(datetime.now(TZ_TAIPEI)),
    }
    for index, (field, value) in enumerate(sorted(updates.items())):
        name_key = f"#f{index}"
        value_key = f":v{index}"
        set_parts.append(f"{name_key} = {value_key}")
        expr_names[name_key] = field
        expr_values[value_key] = prepare_item(value)

    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        resp = table.update_item(
            Key={"elder_id": elder_id, "event_id": event_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ConditionExpression="attribute_exists(event_id) AND #revision = :expected_revision",
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise EventRevisionConflictError(event_id=event_id, expected_revision=expected_revision)
        raise DBError(f"事件 enrichment 失敗: {e.response['Error']['Message']}")
    return convert_decimals(resp.get("Attributes", {}))


def list_events(
    elder_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    next_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """查詢事件時間軸。

    一律走 GSI `events-by-time`：Base table 的 SK 是 `event_id`，用 `ts` 當範圍條件
    在 Base table 上根本無法成立。日期邊界以台灣時間計算，上界補上 `event_time_key`
    的分隔符與最大字元，避免落在 `23:59:59.999` 那一毫秒的事件被排除在外。
    """
    query_kwargs: dict[str, Any] = {
        "IndexName": EVENTS_BY_TIME_INDEX,
        "KeyConditionExpression": "elder_id = :eid",
        "ExpressionAttributeValues": {":eid": elder_id},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    expr_names: dict[str, str] = {}

    if from_date and to_date:
        query_kwargs["KeyConditionExpression"] += (
            " AND event_time_key BETWEEN :start_key AND :end_key"
        )
        query_kwargs["ExpressionAttributeValues"][":start_key"] = day_start(from_date)
        query_kwargs["ExpressionAttributeValues"][":end_key"] = (
            day_end(to_date) + EVENT_TIME_KEY_UPPER_BOUND_SUFFIX
        )

    if event_type:
        query_kwargs["FilterExpression"] = "#t = :etype"
        expr_names["#t"] = "type"
        query_kwargs["ExpressionAttributeValues"][":etype"] = event_type

    if expr_names:
        query_kwargs["ExpressionAttributeNames"] = expr_names
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = get_dynamodb_resource().Table(TABLE_EVENTS).query(**query_kwargs)
    except ClientError as e:
        raise DBError(f"查詢生活事件失敗: {e.response['Error']['Message']}")

    items = convert_decimals(resp.get("Items", []))
    last_key = resp.get("LastEvaluatedKey")
    return items, encode_next_token(last_key) if last_key else None


def _material_differences(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """比對事實欄位差異，判斷既有事件與這次寫入是否互斥。

    刻意不比 `structured_detail`、`confidence`、`evidence_conversation_ids`：
    這些屬於可 enrich 的補充資訊，模型輸出的細微差異不該讓整個 batch 失敗；
    真正互斥的是分類、描述、時間與 routine 對應這些事實。
    """
    differences: dict[str, Any] = {}
    for field in EVENT_MATERIAL_FIELDS:
        before = existing.get(field)
        after = incoming.get(field)
        if after is not None and before != after:
            differences[field] = {"existing": before, "incoming": after}
    return differences


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
    """DEPRECATED：展開指定日期的例行公事行程與動態完成狀態 (pending/done/missed)。

    請改用 `src.shared.routines.list_occurrences()`。這支的假設與真實 schema 不符：
    routines 表是 `routine_id + version` 複合鍵、定義列表走 `routines-current-by-elder`
    GSI，而這裡用 `scan` 加單一 key；也沒有 `occurrence_cutoff` 與 routine 版本解析，
    因此無法滿足 api.md 的 completion-first 規則。待 routines handler 實作時一併移除。

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
    routine_date: str,
    ts: str,
    completed_by: str,
    detail: str = "已確認完成例行公事",
    event_type: str = "other",
    routine_version: int | None = None,
    conversation_id: str | None = None,
    session_id: str | None = None,
    extraction_track: str = "manual",
) -> dict[str, Any]:
    """寫入 canonical routine completion event。

    這支是 completion 的唯一寫入點，由兩個入口共用：realtime rail 在 `/chat` 回應前的
    交易，以及照護者的 `POST /routines/{routine_id}/complete`。canonical key 只由
    `routine_id + routine_date` 決定，因此同日改版、手動與對話完成都收斂到同一筆 event，
    occurrence 是否 `done` 完全由此 event 是否存在判定，`routines` 表不重複保存狀態。

    `routine_version` 只記錄完成當下採用的有效版本，不參與 identity。
    batch 一律不得呼叫（規範：batch 不得建立、修改、停用或完成 routine）。
    """
    if extraction_track == "batch":
        raise DBError("batch 不得寫入 routine completion event")

    canonical_key = routine_completion_key(routine_id, routine_date)
    event = create_event(
        {
            "elder_id": elder_id,
            "canonical_event_key": canonical_key,
            "ts": ts,
            "type": event_type,
            "detail": detail,
            "source": "conversation" if completed_by == "conversation" else "manual",
            "extraction_track": extraction_track,
            "routine_id": routine_id,
            "routine_date": routine_date,
            "routine_version": routine_version,
            "completed_by": completed_by,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "evidence_conversation_ids": [conversation_id] if conversation_id else [],
        }
    )
    return {
        "routine_id": routine_id,
        "routine_date": routine_date,
        "status": "done",
        "completed_at": event["ts"],
        "completed_by": completed_by,
        "event_id": event["event_id"],
    }


# -----------------------------------------------------------------------------
# Daily Summaries 表操作
# -----------------------------------------------------------------------------

def put_daily_summary(summary_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """以覆寫優先序為條件寫入每日摘要；回 `(目前生效的摘要, 是否由本次寫入)`。

    優先序（docs/api.md）：較舊 `input_through_at` 不得覆寫較新 → 同一 cutoff 下
    `complete` 優先於 `partial` → 完整度相同才由較新的 `generated_at` 勝出。

    這必須是條件式寫入而不是「讀出來比一比再寫」：排程與手動生成會並行，讀後判斷會讓
    較舊的結果覆蓋較新的。完整度因此需要可比較的數值 `completeness_rank`
    （`complete=1`、`partial=0`），DynamoDB 的條件運算式只能比屬性值。

    條件不成立不是錯誤，那正是規則生效的樣子（例如手動 partial 想蓋掉排程 complete）；
    此時回傳既有摘要並把 `written=False` 交給呼叫端決定要不要記指標。
    """
    for field_name in ("elder_id", "date", "input_through_at", "generated_at", "data_status"):
        if not summary_data.get(field_name):
            raise DBError(f"儲存每日摘要需要 {field_name}")

    data = dict(summary_data)
    data["completeness_rank"] = 1 if data["data_status"] == "complete" else 0
    data.setdefault("schema_version", 1)

    table = get_dynamodb_resource().Table(TABLE_DAILY_SUMMARIES)
    try:
        table.put_item(
            Item=prepare_item(data),
            ConditionExpression=(
                "attribute_not_exists(elder_id) "
                "OR input_through_at < :cutoff "
                "OR (input_through_at = :cutoff AND completeness_rank < :rank) "
                "OR (input_through_at = :cutoff AND completeness_rank = :rank "
                "AND generated_at <= :generated_at)"
            ),
            ExpressionAttributeValues={
                ":cutoff": data["input_through_at"],
                ":rank": data["completeness_rank"],
                ":generated_at": data["generated_at"],
            },
        )
        return convert_decimals(data), True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise DBError(f"儲存每日摘要失敗: {e.response['Error']['Message']}")

    existing = get_daily_summary(data["elder_id"], data["date"]) or {}
    logger.info(
        "既有摘要較新或完整度較高，保留不覆寫：elder_id=%s date=%s "
        "incoming_cutoff=%s existing_cutoff=%s",
        data["elder_id"],
        data["date"],
        data["input_through_at"],
        existing.get("input_through_at"),
    )
    return existing, False


def get_daily_summary(elder_id: str, date: str) -> dict[str, Any] | None:
    """讀取單日摘要；不存在回 None。"""
    table = get_dynamodb_resource().Table(TABLE_DAILY_SUMMARIES)
    try:
        resp = table.get_item(Key={"elder_id": elder_id, "date": date}, ConsistentRead=True)
    except ClientError as e:
        raise DBError(f"讀取每日摘要失敗: {e.response['Error']['Message']}")
    item = resp.get("Item")
    return convert_decimals(item) if item else None


def list_daily_summaries(
    elder_id: str,
    from_date: str,
    to_date: str,
    limit: int = 50,
    next_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """範圍查詢每日摘要，最新日期優先。

    `date` 是 sort key 且格式固定為 `YYYY-MM-DD`，字串排序即日期排序，因此範圍條件與
    倒序都由 DynamoDB 直接處理，不需要在程式端重排。只回已生成的日期。
    """
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "elder_id = :eid AND #d BETWEEN :from_d AND :to_d",
        "ExpressionAttributeNames": {"#d": "date"},
        "ExpressionAttributeValues": {
            ":eid": elder_id,
            ":from_d": from_date,
            ":to_d": to_date,
        },
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = get_dynamodb_resource().Table(TABLE_DAILY_SUMMARIES).query(**query_kwargs)
    except ClientError as e:
        raise DBError(f"查詢每日摘要失敗: {e.response['Error']['Message']}")

    items = convert_decimals(resp.get("Items", []))
    last_key = resp.get("LastEvaluatedKey")
    return items, encode_next_token(last_key) if last_key else None

