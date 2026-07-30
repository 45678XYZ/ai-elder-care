"""DynamoDB 存取層（docs/framework.md 與 docs/api.md 資料模型）。

提供 6 張表的統一讀寫介面：
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
- 條件式寫入與 transact_write_items（canonical event 冪等、routine 版本推進）
- routine 的 occurrence 狀態不落地，推導邏輯見 src/shared/routines.py
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
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

# canonical 事件身分與時間正規化只有一份實作，避免同一筆資料在兩處算出不同 ID／精度
from src.extraction.canonical import event_id_for, event_time_key, routine_completion_key
from src.extraction.temporal import day_end, day_start, format_ts, normalize_ts
from src.shared.models import ConversationCreate, EventCreate

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

# event_time_key 為 `<ts>#<event_id>`；查詢上界要蓋過同一毫秒的所有 event_id
EVENT_TIME_KEY_UPPER_BOUND_SUFFIX = "#\uffff"

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
    
    # 雙向欄位規範化（確保 elder_transcript / transcript 與 ai_respond_text / reply_text 齊備）
    if "transcript" in data and "elder_transcript" not in data:
        data["elder_transcript"] = data["transcript"]
    elif "elder_transcript" in data and "transcript" not in data:
        data["transcript"] = data["elder_transcript"]

    if "reply_text" in data and "ai_respond_text" not in data:
        data["ai_respond_text"] = data["reply_text"]
    elif "ai_respond_text" in data and "reply_text" not in data:
        data["reply_text"] = data["ai_respond_text"]

    # 自動補全 conversation_id (前綴 cnv_)
    conv_id = data.get("conversation_id")
    if not conv_id:
        conv_id = f"cnv_{uuid.uuid4().hex[:12]}"
        data["conversation_id"] = conv_id
        
    # 自動補全 DynamoDB Sort Key: record_id ("TURN#cnv_...") 與 item_type ("conversation")
    if not data.get("record_id"):
        data["record_id"] = f"TURN#{conv_id}"
    data.setdefault("item_type", "conversation")

    # 自動補全 created_at 與 ts 時間戳記 (+08:00)
    now_iso = datetime.now(TZ_TAIPEI).isoformat()
    if not data.get("created_at"):
        data["created_at"] = now_iso
    if not data.get("ts"):
        data["ts"] = data["created_at"]

    # 自動補全 GSI Sort Key: conversation_time_key (<created_at>#<conversation_id>)
    if not data.get("conversation_time_key"):
        data["conversation_time_key"] = f"{data['created_at']}#{conv_id}"

        
    # 透過 ConversationCreate 進行校驗與預設值補充
    validated = ConversationCreate.model_validate(data)
    validated_dict = validated.model_dump(exclude_none=True)
    data.update(validated_dict)

    try:
        table.put_item(Item=prepare_item(data))
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

def get_event(elder_id: str, event_id: str) -> dict[str, Any] | None:
    """強一致讀取單一事件；不存在回 None。"""
    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        resp = table.get_item(Key={"elder_id": elder_id, "event_id": event_id}, ConsistentRead=True)
    except ClientError as e:
        raise DBError(f"讀取事件紀錄失敗: {e.response['Error']['Message']}")
    item = resp.get("Item")
    return convert_decimals(item) if item else None


def _prepare_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """補齊事件身分（`event_id`／`event_time_key`）與預設欄位。

    `canonical_event_key` 與 `ts` 必填：沒有 canonical key 就沒有穩定事件身分，
    退回隨機 ID 會讓 retry 與 duplicate delivery 各寫一筆；`ts` 也不能用當下時間補，
    否則同一段對話重跑會落到不同 Slot。`event_id` 的推導只有一份實作，
    見 src/extraction/canonical.py。
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
    return data


def create_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """以條件式寫入建立事件；命中相同內容視為冪等，內容互斥則拋出衝突。

    寫入規則見 docs/framework.md 的「Canonical identity 與寫入規則」：
    `attribute_not_exists(event_id)` 條件式 Put，已存在時比對事實欄位，
    完全相同 → 冪等回傳既有資料；互斥 → 保留既有、拋 `EventConflictError` 讓工作失敗告警。

    routine completion 這種「先寫者為準、後到者不算衝突」的情境走 `put_event_if_absent`。
    """
    data = _prepare_event(event_data)
    elder_id = data["elder_id"]
    canonical_key = data["canonical_event_key"]

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


def put_event_if_absent(event_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """條件式寫入事件，回傳 (事件, 是否為本次建立)。

    與 `create_event` 的差別只在後到者的處置：這裡「先寫者為準」，同一 canonical event
    已存在時不比對事實欄位、不視為衝突，直接回既有項目。用於同一件事本來就會由多個入口
    寫入、描述必然不同的情境（例如對話已完成的 routine 又被照護者手動確認）。
    """
    data = _prepare_event(event_data)
    item = prepare_item(data)

    table = get_dynamodb_resource().Table(TABLE_EVENTS)
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(event_id)")
        return convert_decimals(item), True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise DBError(f"建立事件紀錄失敗: {e.response['Error']['Message']}")

    existing = get_event(data["elder_id"], data["event_id"])
    if existing is None:
        raise DBError("事件已存在但讀取失敗")
    return existing, False


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

    canonical key 只由 `routine_id + routine_date` 決定，因此同日改版、手動與對話完成都
    收斂到同一筆 event，occurrence 是否 `done` 完全由此 event 是否存在判定，`routines` 表
    不重複保存狀態。`routine_version` 只記錄完成當下採用的有效版本，不參與 identity。

    batch 一律不得呼叫（規範：batch 不得建立、修改、停用或完成 routine）。
    """
    if extraction_track == "batch":
        raise DBError("batch 不得寫入 routine completion event")

    event, _ = put_event_if_absent(
        {
            "elder_id": elder_id,
            "canonical_event_key": routine_completion_key(routine_id, routine_date),
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


# -----------------------------------------------------------------------------
# Routine 高階便利函式（供 tools handler 與 daily_digest 呼叫）
# 底層讀寫透過 list_routine_versions_by_elder / put_routine_version；
# occurrence 狀態推導由 src/shared/routines.py 完成，不落地。
# -----------------------------------------------------------------------------

def get_daily_routines(elder_id: str, date_str: str) -> dict[str, Any]:
    """查詢指定長者在指定日期的行程清單（含動態完成狀態）。

    回傳格式：
        { "date": "YYYY-MM-DD", "items": [ occurrence, ... ] }

    occurrence 狀態由 routines.resolve_occurrences 推導，
    completion event 透過 completion_event_key 從 events 表查詢。
    """
    from src.shared import routines as rtn

    current = datetime.now(tz=timezone(timedelta(hours=8)))
    upper_bound = rtn.versions_upper_bound(rtn.occurrence_cutoff(date_str, current))

    # 取出當天有效的所有 routine 版本
    versions = list_routine_versions_by_elder(elder_id, upper_bound)

    # 收集對應的 completion event（用穩定 canonical key 查詢）
    completion_event_ids = [
        db_event_id
        for v in versions
        for db_event_id in [
            event_id_for(elder_id, rtn.completion_event_key(v["routine_id"], date_str))
        ]
    ]

    # BatchGet 取完成事件
    completion_map: dict[str, dict[str, Any]] = {}
    if completion_event_ids:
        events_batch = get_events(elder_id, completion_event_ids)
        for evt in events_batch.values():
            rid = evt.get("routine_id")
            if rid:
                completion_map[rid] = evt

    items = rtn.resolve_occurrences(versions, completion_map, date_str, current)
    return {"date": date_str, "items": items}


def create_routine(routine_item: dict[str, Any]) -> dict[str, Any]:
    """建立新的 routine（版本 1，is_current=True）。

    routine_item 應包含：routine_id、elder_id、title、type、schedule、active、created_at。
    """
    from src.shared import routines as rtn

    now_iso = routine_item.get("created_at") or rtn.now_iso()
    version_item = {
        **routine_item,
        "version": 1,
        "is_current": True,
        "effective_from": now_iso,
        "current_sort_key": rtn.current_sort_key(
            routine_item.get("active", True),
            now_iso,
            routine_item["routine_id"],
        ),
        "version_time_key": rtn.version_time_key(now_iso, routine_item["routine_id"], 1),
    }
    return put_routine_version(version_item)


