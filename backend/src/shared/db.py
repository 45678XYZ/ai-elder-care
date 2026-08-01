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
from src.shared.models import (
    DailySummaryCreate,
    EventCreate,
    HealthNote,
    normalize_health_notes_input,
)


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

# 對話時間軸索引；只含 turn，session metadata 沒有 conversation_time_key 因而不入索引
CONVERSATIONS_BY_TIME_INDEX = "conversations-by-time"

# 一次互動只算已完成的 turn：failed 沒有回覆、processing 還沒收斂（見 docs/framework.md）
TURN_STATUS_COMPLETED = "completed"

# health_notes 刪除單筆時的重試上限，見 remove_health_note
_REMOVE_NOTE_MAX_ATTEMPTS = 3

# 兩個時間排序鍵都是 `<時間>#<ID>`（event_time_key、conversation_time_key）；
# 查詢上界要蓋過同一毫秒的所有 ID
TIME_KEY_UPPER_BOUND_SUFFIX = "#\uffff"
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


def _prepare_health_notes(notes: Any) -> list[dict[str, Any]]:
    """把 health_notes 正規化成落地格式：每一筆都帶 note_id、source 與 created_at。

    相容舊資料的純字串（見 models.normalize_health_notes_input）。note_id 在這裡補齊，
    刪除單筆時才有穩定的指定方式——用陣列 index 指定會在併發 append 後指到別人身上。
    """
    normalized = normalize_health_notes_input(notes)
    if not isinstance(normalized, list):
        return []

    now_str = datetime.now(TZ_TAIPEI).isoformat()
    prepared: list[dict[str, Any]] = []
    for item in normalized:
        note = HealthNote.model_validate(item)
        data = note.model_dump()
        if not data.get("created_at"):
            data["created_at"] = now_str
        prepared.append(data)
    return prepared


def append_health_note(elder_id: str, note: Any) -> dict[str, Any]:
    """原子地在 health_notes 尾端加一筆，回傳更新後的長者資料。

    **不做 read-modify-write**：`update_elder` 對 health_notes 是整份 SET，
    照護者在 App 上刪一筆的同時 AI 正好補一筆的話，後寫的會把前一次整份蓋掉。
    這裡改用 DynamoDB 的 list_append，兩邊各自只碰自己那一筆。
    """
    table = get_dynamodb_resource().Table(TABLE_ELDERS)
    prepared = _prepare_health_notes([note])
    if not prepared:
        raise DBError("health_note 內容為空，無法新增")

    try:
        resp = table.update_item(
            Key={"elder_id": elder_id},
            UpdateExpression=(
                "SET #hn = list_append(if_not_exists(#hn, :empty), :new), #ua = :now"
            ),
            ConditionExpression="attribute_exists(elder_id)",
            ExpressionAttributeNames={"#hn": "health_notes", "#ua": "updated_at"},
            ExpressionAttributeValues={
                ":empty": [],
                ":new": prepared,
                ":now": datetime.now(TZ_TAIPEI).isoformat(),
            },
            ReturnValues="ALL_NEW",
        )
        return convert_decimals(resp.get("Attributes", {}))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise DBError(f"找不到長者資料: {elder_id}")
        raise DBError(f"新增健康註記失敗: {e.response['Error']['Message']}")


def remove_health_note(elder_id: str, note_id: str) -> dict[str, Any] | None:
    """依 note_id 刪掉單筆健康註記；找不到該筆時回 None。

    DynamoDB 只能用 index 從 list 移除元素，而 index 會被併發的 append 推移，
    因此每次都重讀出當下的位置，並以「該位置的 note_id 仍是這一筆」為條件寫入。
    條件不成立代表期間有人動過，重讀重試（[_REMOVE_NOTE_MAX_ATTEMPTS] 次）。
    """
    table = get_dynamodb_resource().Table(TABLE_ELDERS)

    for _ in range(_REMOVE_NOTE_MAX_ATTEMPTS):
        elder = get_elder(elder_id)
        if not elder:
            raise DBError(f"找不到長者資料: {elder_id}")

        notes = elder.get("health_notes") or []
        index = next(
            (i for i, n in enumerate(notes) if isinstance(n, dict) and n.get("note_id") == note_id),
            None,
        )
        if index is None:
            return None

        try:
            resp = table.update_item(
                Key={"elder_id": elder_id},
                UpdateExpression=f"REMOVE #hn[{index}] SET #ua = :now",
                ConditionExpression=f"#hn[{index}].#nid = :nid",
                ExpressionAttributeNames={
                    "#hn": "health_notes",
                    "#ua": "updated_at",
                    "#nid": "note_id",
                },
                ExpressionAttributeValues={
                    ":nid": note_id,
                    ":now": datetime.now(TZ_TAIPEI).isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
            return convert_decimals(resp.get("Attributes", {}))
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue  # 位置被併發寫入推移，重讀再來
            raise DBError(f"刪除健康註記失敗: {e.response['Error']['Message']}")

    raise DBError("刪除健康註記失敗：健康註記正被頻繁更新，請稍後再試")


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

    # health_notes 落地前補齊 note_id/source/created_at（也把舊格式的純字串收編）
    data["health_notes"] = _prepare_health_notes(data["health_notes"])


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

    # PATCH 對 health_notes 的語意是整份取代（見 docs/api.md）；這裡只負責補齊每一筆的
    # note_id/source/created_at。要單獨增刪一筆請走 append_health_note／remove_health_note，
    # 它們不會覆寫到別人剛寫進去的內容。
    if data.get("health_notes") is not None:
        data["health_notes"] = _prepare_health_notes(data["health_notes"])

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


def get_recent_conversations(
    elder_id: str, limit: int = 10, next_token: str = None
) -> tuple[list[dict[str, Any]], str | None]:
    """分頁查詢長者近期對話紀錄（按 created_at 時間倒序）。

    走 GSI `conversations-by-time`：Base table 的 SK 是 `record_id`（`TURN#...`／
    `SESSION#...`），字串排序不等於時間排序，倒序查詢取到的不是「最近」的對話。
    GSI 的 SK `conversation_time_key` 為 `<created_at>#<conversation_id>`，字串排序
    即時間排序，`ScanIndexForward=False` 才能真正取到最新優先。

    GSI 設計上只有 turn（session item 不帶 `conversation_time_key`），但仍以
    `FilterExpression` 顯式過濾 `item_type=conversation` 作為防禦，避免未來 schema
    變更時混入 session metadata。
    """
    table = get_dynamodb_resource().Table(TABLE_CONVERSATIONS)

    query_kwargs: dict[str, Any] = {
        "IndexName": CONVERSATIONS_BY_TIME_INDEX,
        "KeyConditionExpression": "elder_id = :eid",
        "FilterExpression": "#it = :conv",
        "ExpressionAttributeNames": {"#it": "item_type"},
        "ExpressionAttributeValues": {":eid": elder_id, ":conv": "conversation"},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if next_token:
        query_kwargs["ExclusiveStartKey"] = decode_next_token(next_token)

    try:
        resp = table.query(**query_kwargs)
    except ClientError as e:
        raise DBError(f"查詢近期對話紀錄失敗: {e.response['Error']['Message']}")

    items = convert_decimals(resp.get("Items", []))
    last_key = resp.get("LastEvaluatedKey")
    return items, encode_next_token(last_key) if last_key else None


def list_turn_times(elder_id: str, from_date: str, to_date: str) -> list[str]:
    """取期間內已完成 turn 的 `created_at`（遞增），供統計計算對話輪數。

    走 GSI `conversations-by-time`：Base table 的 SK 是 `record_id`，時間範圍查詢在 Base
    table 上無法成立，且該索引只含 turn，session metadata 不會被算成一次互動。

    只投影時間戳：統計要的是「幾輪、最後一輪在何時」，把逐字稿讀進 Lambda 只是多搬一份
    PII。統計必須涵蓋整個區間，因此分頁全部讀完，不像列表 API 只回一頁。
    """

    table = get_dynamodb_resource().Table(TABLE_CONVERSATIONS)
    query_kwargs: dict[str, Any] = {
        "IndexName": CONVERSATIONS_BY_TIME_INDEX,
        "KeyConditionExpression": (
            "elder_id = :eid AND conversation_time_key BETWEEN :start_key AND :end_key"
        ),
        "FilterExpression": "request_status = :completed",
        "ProjectionExpression": "created_at",
        "ExpressionAttributeValues": {
            ":eid": elder_id,
            ":start_key": day_start(from_date),
            ":end_key": day_end(to_date) + TIME_KEY_UPPER_BOUND_SUFFIX,
            ":completed": TURN_STATUS_COMPLETED,
        },
        "ScanIndexForward": True,
    }

    times: list[str] = []
    try:
        while True:
            resp = table.query(**query_kwargs)
            times.extend(item["created_at"] for item in resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
    except ClientError as e:
        raise DBError(f"查詢對話輪數失敗: {e.response['Error']['Message']}")

    return times


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

    # 透過 EventCreate 校驗事件型別與必填欄位
    try:
        validated = EventCreate.model_validate(data)
        validated_dict = validated.model_dump(exclude_none=True)
        data.update(validated_dict)
    except Exception as exc:
        raise DBError(f"建立事件校驗失敗: {exc}")

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
    unique_ids = list(dict.fromkeys(event_ids))

    # BatchGetItem 單次上限 100 筆；被節流的 key 有限次重取，避免 Lambda 卡到逾時
    for start in range(0, len(unique_ids), 100):
        keys = [{"elder_id": elder_id, "event_id": eid} for eid in unique_ids[start:start + 100]]

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
            day_end(to_date) + TIME_KEY_UPPER_BOUND_SUFFIX
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


def delete_routine(routine_id: str) -> None:
    """刪除 routine 的所有版本（真刪除，非 soft-delete）。

    事件表中的 routine_completion 記錄不受影響：那些是歷史事實。
    若 routine 不存在則靜默返回（冪等）。
    """
    versions = list_routine_versions(routine_id)
    if not versions:
        return

    table = get_dynamodb_resource().Table(TABLE_ROUTINES)
    with table.batch_writer() as batch:
        for v in versions:
            batch.delete_item(Key={"routine_id": routine_id, "version": int(v["version"])})


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
    # 透過 DailySummaryCreate 校驗必填欄位與型別，與 save_conversation / _prepare_event 一致
    try:
        validated = DailySummaryCreate.model_validate(summary_data)
        data = validated.model_dump(exclude_none=True)
    except Exception as exc:
        raise DBError(f"儲存每日摘要校驗失敗: {exc}")

    # completeness_rank 由 data_status 推導，不暴露於 schema；schema_version 為內部欄位
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
    next_token = encode_next_token(resp["LastEvaluatedKey"]) if "LastEvaluatedKey" in resp else None
    return items, next_token






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


