"""Session 生命週期與 batch 狀態機。

規範見 docs/framework.md 的「Session metadata 欄位」與「Session close、SQS recovery 與 DLQ」。
這一層的存在理由是：session 是 immutable input snapshot 的邊界，而 batch 的冪等性完全建立在
「狀態轉換都用條件式寫入」上。任何一處改成無條件更新，就會出現兩個 worker 同時處理同一個
snapshot、或 DLQ replay 覆寫掉已完成結果。

狀態轉換（每一步都是條件式）：

    active ──(inflight=0)──▶ closing ──(freeze+hash)──▶ closed / batch=pending
    pending ──(claim)──▶ processing ──(owner 相符)──▶ completed
                              └──(permanent error)──▶ failed

`failed` 不供正常 worker 或自動 recovery claim，只能由人工 replay 先轉回 `pending`。
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
import hashlib
import json
import logging
import uuid


from botocore.exceptions import ClientError

from src.extraction.temporal import TZ_TAIPEI, format_ts, parse_ts

from . import db

logger = logging.getLogger(__name__)

SESSION_RECORD_PREFIX = "SESSION#"
TURN_RECORD_PREFIX = "TURN#"
SESSIONS_BY_STATE_INDEX = "sessions-by-state"

STATE_ACTIVE = "active"
STATE_CLOSING = "closing"
STATE_CLOSED = "closed"

BATCH_PENDING = "pending"
BATCH_PROCESSING = "processing"
BATCH_COMPLETED = "completed"
BATCH_FAILED = "failed"

STATE_KEY_ACTIVE = "ACTIVE"
STATE_KEY_BATCH_PENDING = "BATCH#PENDING"
STATE_KEY_BATCH_PROCESSING = "BATCH#PROCESSING"
STATE_KEY_BATCH_FAILED = "BATCH#FAILED"

# claim 結果；handler 依此決定執行、直接 ack 或 throw
CLAIM_ACQUIRED = "acquired"
CLAIM_ALREADY_COMPLETED = "already_completed"
CLAIM_ALREADY_FAILED = "already_failed"
CLAIM_LEASE_ACTIVE = "lease_active"
CLAIM_SNAPSHOT_MISMATCH = "snapshot_mismatch"
CLAIM_NOT_FOUND = "not_found"
CLAIM_NOT_CLOSED = "not_closed"


class SessionError(db.DBError):
    """session 狀態轉換不合法。"""


class SessionNotFoundError(SessionError):
    """session 不存在或不屬於該長者。"""


class SessionInflightError(SessionError):
    """session 仍有未收斂的 inflight turn，close 必須等待或接管。"""


def session_record_id(session_id: str) -> str:
    return f"{SESSION_RECORD_PREFIX}{session_id}"


def _now() -> datetime:
    return datetime.now(TZ_TAIPEI)


def compute_snapshot_hash(turn_ids: Sequence[str], input_bytes: int) -> str:
    """frozen 輸入的穩定雜湊。

    以 canonical serialization（固定順序、固定分隔）計算，讓 DLQ replay 能靠它判斷
    「手上的訊息是否對應目前的 frozen snapshot」。turn 順序是輸入的一部分，因此不排序。
    """
    payload = json.dumps(
        {"turn_ids": list(turn_ids), "input_bytes": int(input_bytes)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_session(elder_id: str, session_id: str) -> dict[str, Any] | None:
    """讀取 session metadata；不存在回 None。"""
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.get_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            ConsistentRead=True,
        )
    except ClientError as exc:
        raise SessionError(f"讀取 session 失敗: {exc.response['Error']['Message']}")
    item = response.get("Item")
    return db.convert_decimals(item) if item else None


def put_session(session: dict[str, Any]) -> dict[str, Any]:
    """寫入 session metadata（建立與測試 seed 用）。"""
    item = dict(session)
    item.setdefault("item_type", "session")
    item.setdefault("record_id", session_record_id(item["session_id"]))
    item.setdefault("schema_version", 1)
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        table.put_item(Item=db.prepare_item(item))
    except ClientError as exc:
        raise SessionError(f"寫入 session 失敗: {exc.response['Error']['Message']}")
    return db.convert_decimals(item)


def is_pending_materialization(session: dict[str, Any]) -> bool:
    """這個 session 是否仍可能產生尚未寫入的一般事件。

    摘要的 `data_status` 完全建立在這個判斷上：`active`／`closing` 還會長出新 turn；
    `closed` 但 batch 尚未 `completed` 表示一般事件還沒 materialize。`batch_status` 缺值
    時保守視為未完成——寧可標成 `partial` 再重算，也不要對照護者宣稱資料已完整。
    """
    state = session.get("state")
    if state in (STATE_ACTIVE, STATE_CLOSING):
        return True
    if state == STATE_CLOSED:
        return (session.get("batch_status") or BATCH_PENDING) != BATCH_COMPLETED
    # 未知狀態同樣保守處理
    return True


def list_pending_sessions(elder_id: str, session_ids: Sequence[str]) -> list[dict[str, Any]]:
    """對候選 session 逐一強一致讀取，回仍待 materialize 的那些。

    候選來自 `conversations-by-time` GSI（最終一致），因此狀態一律回 Base table 強一致讀，
    符合 framework 的「GSI 只用來找候選」。
    """
    pending: list[dict[str, Any]] = []
    for session_id in dict.fromkeys(session_ids):
        session = get_session(elder_id, session_id)
        if session is None:
            # turn 指向不存在的 session：資料不一致，保守計入待處理並記錄
            logger.warning(
                "turn 指向不存在的 session：elder_id=%s session_id=%s", elder_id, session_id
            )
            pending.append({"session_id": session_id, "state": None})
            continue
        if is_pending_materialization(session):
            pending.append(session)
    return pending


def list_sessions_by_state(
    state_key: str,
    *,
    before: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """由 sparse GSI 找候選 session。

    `ACTIVE` 供 idle close sweep，`BATCH#PENDING` 供派送遺漏的補投，
    `BATCH#PROCESSING` 供 lease-expired 接管。`BATCH#FAILED` 只給人工工具用。
    """
    query: dict[str, Any] = {
        "IndexName": SESSIONS_BY_STATE_INDEX,
        "KeyConditionExpression": "session_state_key = :state",
        "ExpressionAttributeValues": {":state": state_key},
        "ScanIndexForward": True,
        "Limit": limit,
    }
    if before:
        query["KeyConditionExpression"] += " AND session_state_time_key < :before"
        query["ExpressionAttributeValues"][":before"] = before

    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.query(**query)
    except ClientError as exc:
        raise SessionError(f"查詢 session 狀態索引失敗: {exc.response['Error']['Message']}")
    return db.convert_decimals(response.get("Items", []))


# -----------------------------------------------------------------------------
# close：active → closing → closed
# -----------------------------------------------------------------------------


def begin_closing(
    elder_id: str,
    session_id: str,
    *,
    close_reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把 session 推進 `closing` 並凍結輸入。

    條件包含 `inflight_turn_count = 0`：還有 turn 在飛的時候凍結，會凍出一份少了那些 turn
    的 snapshot，之後 batch 就永久漏掉它們。`active` 與 `closing` 都允許（close 是冪等的）。
    """
    moment = now or _now()
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET #state = :closing, close_reason = if_not_exists(close_reason, :reason), "
                "last_activity_at = :now"
            ),
            ConditionExpression=(
                "attribute_exists(record_id) AND #state IN (:active, :closing) "
                "AND inflight_turn_count = :zero"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":closing": STATE_CLOSING,
                ":active": STATE_ACTIVE,
                ":reason": close_reason,
                ":zero": 0,
                ":now": format_ts(moment),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"session 轉入 closing 失敗: {exc.response['Error']['Message']}")
        _raise_close_conflict(elder_id, session_id)
        # 走到這裡代表已是 closed；close 是冪等操作，回目前狀態即可
        return get_session(elder_id, session_id) or {}
    return db.convert_decimals(response.get("Attributes", {}))


def _raise_close_conflict(elder_id: str, session_id: str) -> None:
    """條件不成立時區分「不存在」「已 closed」「仍有 inflight」。"""
    session = get_session(elder_id, session_id)
    if session is None:
        raise SessionNotFoundError("session 不存在或不屬於該長者")
    if session.get("state") == STATE_CLOSED:
        # closed 是終態，重複 close 視為冪等，由呼叫端處理
        return
    if int(session.get("inflight_turn_count") or 0) > 0:
        raise SessionInflightError("session 仍有 inflight turn")
    raise SessionError(f"session 狀態不允許 close：state={session.get('state')}")


def finalize_closed(
    elder_id: str,
    session_id: str,
    *,
    turn_ids: Sequence[str],
    input_bytes: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """驗證後把 session 推進 `closed` 並排入 batch。

    在同一次更新裡寫 `session_snapshot_hash`、`closed_at`、`batch_status=pending` 與
    `BATCH#PENDING` GSI 欄位：closed 與待派送必須一起成立，否則會出現「已 closed 但沒人
    知道要跑 batch」的 session。
    """
    moment = now or _now()
    closed_at = format_ts(moment)
    snapshot_hash = compute_snapshot_hash(turn_ids, input_bytes)

    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET #state = :closed, closed_at = :closed_at, turn_ids = :turn_ids, "
                "turn_count = :turn_count, input_bytes = :input_bytes, "
                "session_snapshot_hash = :hash, batch_status = :pending, "
                "batch_attempts = if_not_exists(batch_attempts, :zero), "
                "session_state_key = :state_key, session_state_time_key = :state_time"
            ),
            ConditionExpression=(
                "attribute_exists(record_id) AND #state IN (:closing, :closed) "
                "AND inflight_turn_count = :zero"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":closed": STATE_CLOSED,
                ":closing": STATE_CLOSING,
                ":closed_at": closed_at,
                ":turn_ids": list(turn_ids),
                ":turn_count": len(turn_ids),
                ":input_bytes": int(input_bytes),
                ":hash": snapshot_hash,
                ":pending": BATCH_PENDING,
                ":zero": 0,
                ":state_key": STATE_KEY_BATCH_PENDING,
                ":state_time": f"{closed_at}#{elder_id}#{session_id}",
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"session 轉入 closed 失敗: {exc.response['Error']['Message']}")
        _raise_close_conflict(elder_id, session_id)
        return get_session(elder_id, session_id) or {}
    return db.convert_decimals(response.get("Attributes", {}))


# -----------------------------------------------------------------------------
# chunk manifest
# -----------------------------------------------------------------------------


def persist_chunk_manifest(
    elder_id: str,
    session_id: str,
    manifest: Sequence[dict[str, Any]],
    *,
    planner_version: str,
) -> list[dict[str, Any]]:
    """首次成功的 manifest 條件式持久化；已存在則回既有值。

    這是「分塊允許非確定性」與「batch 必須冪等」能並存的關鍵：只要第一次寫入後所有
    retry、duplicate delivery 與 DLQ replay 都重用同一份，chunk ID 就不會漂移。
    """
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET chunk_manifest = :manifest, chunk_planner_version = :planner_version"
            ),
            ConditionExpression="attribute_not_exists(chunk_manifest)",
            ExpressionAttributeValues={
                ":manifest": db.prepare_item(list(manifest)),
                ":planner_version": planner_version,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"寫入 chunk manifest 失敗: {exc.response['Error']['Message']}")
        existing = get_session(elder_id, session_id) or {}
        logger.info("chunk manifest 已存在，重用既有規劃：session_id=%s", session_id)
        return existing.get("chunk_manifest") or []
    return db.convert_decimals(response.get("Attributes", {})).get("chunk_manifest") or []


# -----------------------------------------------------------------------------
# batch 狀態機
# -----------------------------------------------------------------------------


def claim_batch(
    elder_id: str,
    session_id: str,
    *,
    snapshot_hash: str,
    owner: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """嘗試取得 batch 處理權，回傳 `(結果, session)`。

    可 claim 的只有兩種情況：`pending`，或 `processing` 但 lease 已過期（前一個 worker 死了）。
    `completed` 與 `failed` 都不可 claim——重複投遞在那兩種狀態下應直接 ack，
    否則已完成的結果會被重跑覆寫、人工待處理的 failed 會被自動流程搶走。
    """
    moment = now or _now()
    lease_until = format_ts(moment + timedelta(seconds=lease_seconds))

    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET batch_status = :processing, batch_lease_owner = :owner, "
                "batch_lease_until = :lease_until, session_state_key = :state_key, "
                "session_state_time_key = :state_time, "
                "batch_attempts = if_not_exists(batch_attempts, :zero) + :one"
            ),
            ConditionExpression=(
                "attribute_exists(record_id) AND #state = :closed "
                "AND session_snapshot_hash = :hash "
                "AND (batch_status = :pending "
                "OR (batch_status = :processing AND batch_lease_until < :now))"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":processing": BATCH_PROCESSING,
                ":pending": BATCH_PENDING,
                ":closed": STATE_CLOSED,
                ":hash": snapshot_hash,
                ":owner": owner,
                ":lease_until": lease_until,
                ":now": format_ts(moment),
                ":state_key": STATE_KEY_BATCH_PROCESSING,
                ":state_time": f"{lease_until}#{elder_id}#{session_id}",
                ":zero": 0,
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"claim batch 失敗: {exc.response['Error']['Message']}")
        return _classify_claim_failure(elder_id, session_id, snapshot_hash), {}
    return CLAIM_ACQUIRED, db.convert_decimals(response.get("Attributes", {}))


def _classify_claim_failure(elder_id: str, session_id: str, snapshot_hash: str) -> str:
    session = get_session(elder_id, session_id)
    if session is None:
        return CLAIM_NOT_FOUND
    if session.get("state") != STATE_CLOSED:
        return CLAIM_NOT_CLOSED
    if session.get("session_snapshot_hash") != snapshot_hash:
        return CLAIM_SNAPSHOT_MISMATCH
    status = session.get("batch_status")
    if status == BATCH_COMPLETED:
        return CLAIM_ALREADY_COMPLETED
    if status == BATCH_FAILED:
        return CLAIM_ALREADY_FAILED
    return CLAIM_LEASE_ACTIVE


def complete_batch(
    elder_id: str,
    session_id: str,
    *,
    owner: str,
    extractor_version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """標記 batch 完成並移除 GSI 欄位與 lease。

    條件包含 lease owner 相符：lease 過期後被別人接管時，原 owner 遲到的完成不可覆寫。
    移除 `session_state_key`／`session_state_time_key` 是為了讓 sparse GSI 只留待處理項目。
    """
    moment = now or _now()
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET batch_status = :completed, batch_completed_at = :now, "
                "batch_extractor_version = :version "
                "REMOVE session_state_key, session_state_time_key, batch_lease_owner, "
                "batch_lease_until, batch_error"
            ),
            ConditionExpression=(
                "attribute_exists(record_id) AND batch_status = :processing "
                "AND batch_lease_owner = :owner"
            ),
            ExpressionAttributeValues={
                ":completed": BATCH_COMPLETED,
                ":processing": BATCH_PROCESSING,
                ":owner": owner,
                ":now": format_ts(moment),
                ":version": extractor_version,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"完成 batch 失敗: {exc.response['Error']['Message']}")
        raise SessionError("batch 完成條件不成立（lease 已被接管或狀態已改變）")
    return db.convert_decimals(response.get("Attributes", {}))


def fail_batch(
    elder_id: str,
    session_id: str,
    *,
    owner: str | None,
    code: str,
    message: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把 batch 標為 `failed`（僅 permanent 錯誤）。

    `batch_error` 存安全化後的代碼與訊息，不夾帶逐字稿。condition 排除 `completed`，
    避免遲到的失敗覆寫已完成的結果。
    """
    moment = now or _now()
    failed_at = format_ts(moment)
    values: dict[str, Any] = {
        ":failed": BATCH_FAILED,
        ":completed": BATCH_COMPLETED,
        ":state_key": STATE_KEY_BATCH_FAILED,
        ":state_time": f"{failed_at}#{elder_id}#{session_id}",
        ":error": {"code": code, "message": message[:500], "failed_at": failed_at},
    }
    condition = "attribute_exists(record_id) AND batch_status <> :completed"
    if owner is not None:
        condition += " AND batch_lease_owner = :owner"
        values[":owner"] = owner

    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET batch_status = :failed, batch_error = :error, "
                "session_state_key = :state_key, session_state_time_key = :state_time "
                "REMOVE batch_lease_owner, batch_lease_until"
            ),
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"標記 batch 失敗時出錯: {exc.response['Error']['Message']}")
        raise SessionError("batch 失敗條件不成立（可能已完成或 lease 已被接管）")
    return db.convert_decimals(response.get("Attributes", {}))


def release_batch_lease(
    elder_id: str,
    session_id: str,
    *,
    owner: str,
    now: datetime | None = None,
) -> None:
    """retryable 失敗時放掉 lease 並回到 `pending`。

    不設 `failed`：那是 permanent 錯誤專用。回到 pending 讓 SQS 重投或 recovery sweep
    立刻能接手，而不用等 lease 自然過期。
    """
    moment = now or _now()
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET batch_status = :pending, session_state_key = :state_key, "
                "session_state_time_key = :state_time "
                "REMOVE batch_lease_owner, batch_lease_until"
            ),
            ConditionExpression="batch_status = :processing AND batch_lease_owner = :owner",
            ExpressionAttributeValues={
                ":pending": BATCH_PENDING,
                ":processing": BATCH_PROCESSING,
                ":owner": owner,
                ":state_key": STATE_KEY_BATCH_PENDING,
                ":state_time": f"{format_ts(moment)}#{elder_id}#{session_id}",
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"釋放 batch lease 失敗: {exc.response['Error']['Message']}")
        logger.info("lease 已不屬於本 worker，無須釋放：session_id=%s owner=%s", session_id, owner)


def requeue_failed_batch(
    elder_id: str,
    session_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """人工 replay：`failed → pending`。

    刻意不重建 manifest：replay 必須沿用 frozen state 與既有 manifest，否則 chunk ID
    會變、已寫入的事件會對不上來源。
    """
    moment = now or _now()
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET batch_status = :pending, session_state_key = :state_key, "
                "session_state_time_key = :state_time "
                "REMOVE batch_lease_owner, batch_lease_until"
            ),
            ConditionExpression="batch_status = :failed",
            ExpressionAttributeValues={
                ":pending": BATCH_PENDING,
                ":failed": BATCH_FAILED,
                ":state_key": STATE_KEY_BATCH_PENDING,
                ":state_time": f"{format_ts(moment)}#{elder_id}#{session_id}",
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise SessionError(f"replay batch 失敗: {exc.response['Error']['Message']}")
        raise SessionError("只有 failed 的 batch 可以 replay")
    return db.convert_decimals(response.get("Attributes", {}))


# -----------------------------------------------------------------------------
# frozen turns
# -----------------------------------------------------------------------------


def get_frozen_turns(elder_id: str, turn_ids: Sequence[str]) -> list[dict[str, Any]]:
    """以強一致讀取 frozen turns，並依 `turn_ids` 的順序回傳。

    順序是 snapshot 的一部分（也是 snapshot hash 的輸入），所以不能靠 DynamoDB 的回傳順序。
    """
    if not turn_ids:
        return []

    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(turn_ids), 100):
        batch = turn_ids[start : start + 100]
        keys = [
            {"elder_id": elder_id, "record_id": f"{TURN_RECORD_PREFIX}{turn_id}"}
            for turn_id in batch
        ]
        try:
            for item in _batch_get_all(keys):
                found[item["record_id"]] = db.convert_decimals(item)
        except ClientError as exc:
            raise SessionError(f"讀取 frozen turns 失敗: {exc.response['Error']['Message']}")

    ordered = []
    for turn_id in turn_ids:
        item = found.get(f"{TURN_RECORD_PREFIX}{turn_id}")
        if item is None:
            raise SessionError(f"frozen snapshot 缺少 turn：{turn_id}")
        ordered.append(item)
    return ordered


def _batch_get_all(keys: list[dict[str, str]]) -> list[dict[str, Any]]:
    """BatchGet 並處理 UnprocessedKeys。"""
    resource = db.get_dynamodb_resource()
    pending = list(keys)
    items: list[dict[str, Any]] = []
    while pending:
        response = resource.batch_get_item(
            RequestItems={
                db.TABLE_CONVERSATIONS: {"Keys": pending, "ConsistentRead": True}
            }
        )
        items.extend(response.get("Responses", {}).get(db.TABLE_CONVERSATIONS, []))
        unprocessed = response.get("UnprocessedKeys", {}).get(db.TABLE_CONVERSATIONS, {})
        pending = unprocessed.get("Keys", []) if unprocessed else []
    return items


def mark_turns_batch_completed(
    elder_id: str,
    chunk_by_turn: dict[str, str],
    *,
    extractor_version: str,
    now: datetime | None = None,
) -> int:
    """標記 turn 萃取完成（極簡架構：所有狀態集中維護於 Session 的 `chunk_manifest`）。"""
    return len(chunk_by_turn)



def is_lease_expired(session: dict[str, Any], *, now: datetime | None = None) -> bool:
    """判斷 lease 是否已過期（供 recovery sweep 篩選）。"""
    lease_until = session.get("batch_lease_until")
    if not lease_until:
        return True
    return parse_ts(lease_until) < (now or _now())


def create_session(elder_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """建立新的 active session 實體。"""
    moment = now or _now()
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    created_at = format_ts(moment)
    item = {
        "elder_id": elder_id,
        "record_id": session_record_id(session_id),
        "session_id": session_id,
        "item_type": "session",
        "state": STATE_ACTIVE,
        "session_state_key": STATE_KEY_ACTIVE,
        "session_state_time_key": f"{created_at}#{elder_id}#{session_id}",
        "created_at": created_at,
        "last_activity_at": created_at,
        "turn_count": 0,
        "schema_version": 1,
    }
    return put_session(item)


def resolve_session_for_chat(
    elder_id: str,
    requested_session_id: str | None = None,
    *,
    idle_minutes: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    """依 docs/api.md 選擇既有 active session 或建立新 session。"""
    moment = now or _now()
    if requested_session_id:
        existing = get_session(elder_id, requested_session_id)
        if existing is None:
            raise SessionNotFoundError(f"session 不存在：{requested_session_id}")
        if existing.get("elder_id") != elder_id:
            raise SessionError("FORBIDDEN")

        # 檢查是否能沿用：必須是 active 且未超過 idle 門檻
        if existing.get("state") == STATE_ACTIVE:
            last_act = existing.get("last_activity_at") or existing.get("created_at")
            if last_act:
                last_act_dt = parse_ts(last_act)
                if (moment - last_act_dt) <= timedelta(minutes=idle_minutes):
                    return existing

    # 第一輪未帶 session_id，或既有 session 已 closed / idle 超時 ➔ 自動開立新 active session
    return create_session(elder_id, now=moment)


def touch_session_activity(elder_id: str, session_id: str, *, now: datetime | None = None) -> None:
    """更新 session 之 last_activity_at 與 turn_count。"""
    moment = now or _now()
    ts_str = format_ts(moment)
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        table.update_item(
            Key={"elder_id": elder_id, "record_id": session_record_id(session_id)},
            UpdateExpression=(
                "SET last_activity_at = :ts, "
                "session_state_time_key = :state_time, "
                "turn_count = if_not_exists(turn_count, :zero) + :one"
            ),
            ConditionExpression="attribute_exists(record_id) AND #state = :active",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":ts": ts_str,
                ":state_time": f"{ts_str}#{elder_id}#{session_id}",
                ":active": STATE_ACTIVE,
                ":zero": 0,
                ":one": 1,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            logger.warning("更新 session activity 失敗: %s", exc.response["Error"]["Message"])

