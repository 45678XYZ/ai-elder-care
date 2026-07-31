"""Turn 請求狀態機（`processing` → `completed` ／ `failed`）。

規格見 docs/framework.md 的「Turn 欄位」與 docs/api.md 的 `POST /chat` routing 規則。

存在理由：`/chat` 是唯一在 response 前就產生副作用的路徑（routine 建立、修改、停用、完成，
以及高風險 safety event）。手機在電梯裡送出、API Gateway 逾時重試、使用者按兩次送出，
都會讓同一句話進來兩次；沒有這一層，一句「我吃過血壓藥了」就會寫成兩輪對話、兩次完成。

狀態與唯一的合法轉移：

    (不存在) ──reserve──▶ processing ──commit──▶ completed   穩定結果，可無限重播
                              └────fail─────────▶ failed      穩定錯誤，重試須換新的 client_request_id

三個設計選擇撐起冪等性：

- **身分穩定**：`conversation_id` 由 `elder_id + idempotency_key` 穩定產生，因此「同一個請求」
  重送時必然指向同一筆 item，冪等判定只是一次強一致 GetItem，不需要額外索引或掃描。
- **request lease**：`processing` 帶租約。租約未到期代表另一個 invocation 還在飛，重送回 409；
  到期才可接管，讓「Lambda 被砍掉」的 turn 不會永遠卡住 session 的 inflight reservation。
- **reserve／commit 都是單一 transaction**：turn 與 session 的 inflight reservation 一起成立、
  一起解除。任何一半的條件不成立就整筆取消，不會留下「有副作用但沒紀錄」的中間狀態。
"""

from datetime import datetime, timedelta
import logging
import os
from typing import Any

from botocore.exceptions import ClientError

from src.extraction.temporal import TZ_TAIPEI, format_ts, parse_ts
from src.shared import db, routines, sessions

logger = logging.getLogger(__name__)

STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)

# request lease 長度。必須大於 /chat Lambda 的 timeout，否則同一個請求還在跑就被判定為
# 「前一個 invocation 已死」而被接管，兩邊會同時做副作用。
REQUEST_LEASE_SECONDS = int(os.environ.get("REQUEST_LEASE_SECONDS", "60"))

# 計入 session `input_bytes` 的欄位；與 session close 的 snapshot 驗證共用同一份定義，
# 兩處算法不同會讓 close 永遠對不上 session 上累計的數字。
INPUT_BYTE_FIELDS: tuple[str, ...] = ("ai_prompt_text", "elder_transcript", "ai_respond_text")

# reserve 時必須由呼叫端指定的欄位；其餘由本模組補齊
_REQUIRED_TURN_FIELDS: tuple[str, ...] = (
    "conversation_id",
    "client_request_id",
    "request_hash",
    "lang",
    "input_type",
)



class TurnError(db.DBError):
    """turn 狀態轉移不合法。"""


class TurnConflictError(TurnError):
    """相同冪等鍵已用於不同內容的請求（idempotency conflict）。"""


class TurnInProgressError(TurnError):
    """相同請求仍在處理中且 request lease 尚未到期。"""


class TurnReserveRejectedError(TurnError):
    """session 已不接受新 turn（已 closing／closed 或達上限），呼叫端應改用新 session。"""


class TurnTransitionRejectedError(TurnError):
    """turn 已不是預期的狀態或已不是下一個可提交的 reservation。"""


def _now() -> datetime:
    return datetime.now(TZ_TAIPEI)


def turn_record_id(conversation_id: str) -> str:
    return f"{sessions.TURN_RECORD_PREFIX}{conversation_id}"


def conversation_id_for(elder_id: str, idempotency_key: str) -> str:
    """由冪等鍵穩定產生 turn ID：同一長者的同一請求重送必然得到同一個 ID。"""
    return routines.stable_id("cnv_", elder_id, idempotency_key)


def request_hash(payload: dict[str, Any]) -> str:
    """正規化請求內容的 hash，供同一冪等鍵判斷是重送或衝突。"""
    return routines.request_hash(payload)


def input_bytes_of(turn: dict[str, Any]) -> int:
    """turn 的正規化輸入位元組數。"""
    return sum(len((turn.get(field) or "").encode("utf-8")) for field in INPUT_BYTE_FIELDS)


def get_turn(elder_id: str, conversation_id: str) -> dict[str, Any] | None:
    """強一致讀取 turn；不存在回 None。

    冪等判定不能讀最終一致的資料：重送常常在毫秒內就到，讀到「還沒有」就會重跑一次副作用。
    """
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.get_item(
            Key={"elder_id": elder_id, "record_id": turn_record_id(conversation_id)},
            ConsistentRead=True,
        )
    except ClientError as exc:
        raise TurnError(f"讀取 turn 失敗: {exc.response['Error']['Message']}")
    item = response.get("Item")
    return db.convert_decimals(item) if item else None


def is_lease_expired(turn: dict[str, Any], *, now: datetime | None = None) -> bool:
    """request lease 是否已到期（沒有租約欄位視為已到期，可接管）。"""
    lease_until = turn.get("request_lease_until")
    if not lease_until:
        return True
    return parse_ts(lease_until) < (now or _now())


def assert_same_request(turn: dict[str, Any], expected_hash: str) -> None:
    """同一冪等鍵搭配不同內容是衝突，不是重送；此時不得回既有結果。"""
    if turn.get("request_hash") != expected_hash:
        raise TurnConflictError("相同的 client_request_id 已用於不同內容的請求")


# -----------------------------------------------------------------------------
# reserve：建立 processing turn 並佔用 session 的 inflight 名額
# -----------------------------------------------------------------------------


def reserve(
    elder_id: str,
    session_id: str,
    *,
    turn: dict[str, Any],
    owner: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """以單一 transaction 建立 `processing` turn 並 reserve session 的 inflight 名額。

    兩件事必須同時成立：turn 尚不存在（否則是重送，該走冪等判定而不是再寫一次），
    且 session 仍為 `active` 並有名額。session 條件不成立時拋
    `TurnReserveRejectedError`，由呼叫端改用新 session——不可退而求其次寫進已 closing 的
    session，那會讓已凍結的 snapshot 少掉這個 turn。
    """
    missing = [field for field in _REQUIRED_TURN_FIELDS if not turn.get(field)]
    if missing:
        raise TurnError(f"reserve turn 缺少必要欄位：{', '.join(missing)}")

    moment = now or _now()
    created_at = format_ts(moment)
    conversation_id = turn["conversation_id"]
    item = {
        "item_type": "conversation",
        "source": "elder_initiated",
        "user_status": "replied",
        "routines_updated": False,
        "schema_version": 1,
        **turn,

        "elder_id": elder_id,
        "record_id": turn_record_id(conversation_id),
        "conversation_time_key": f"{created_at}#{conversation_id}",
        "created_at": created_at,
        "session_id": session_id,
        "request_status": STATUS_PROCESSING,
        "request_lease_owner": owner,
        "request_lease_until": format_ts(moment + timedelta(seconds=REQUEST_LEASE_SECONDS)),
    }

    try:
        db.get_dynamodb_client().transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": db.TABLE_CONVERSATIONS,
                        "Item": db.to_attribute_values(db.prepare_item(item)),
                        "ConditionExpression": "attribute_not_exists(record_id)",
                    }
                },
                {
                    "Update": {
                        "TableName": db.TABLE_CONVERSATIONS,
                        "Key": db.to_attribute_values(
                            {
                                "elder_id": elder_id,
                                "record_id": sessions.session_record_id(session_id),
                            }
                        ),
                        "UpdateExpression": (
                            "SET inflight_turn_ids = "
                            "list_append(if_not_exists(inflight_turn_ids, :empty), :ids), "
                            "inflight_turn_count = inflight_turn_count + :one, "
                            "last_activity_at = :now, session_state_time_key = :state_time"
                        ),
                        "ConditionExpression": (
                            "attribute_exists(record_id) AND #state = :active "
                            "AND inflight_turn_count < :max_inflight "
                            "AND turn_count < :max_turns "
                            "AND input_bytes < :max_bytes"
                        ),
                        "ExpressionAttributeNames": {"#state": "state"},
                        "ExpressionAttributeValues": db.to_attribute_values(
                            {
                                ":ids": [conversation_id],
                                ":empty": [],
                                ":one": 1,
                                ":now": created_at,
                                ":state_time": f"{created_at}#{elder_id}#{session_id}",
                                ":active": sessions.STATE_ACTIVE,
                                ":max_inflight": sessions.SESSION_MAX_INFLIGHT_TURNS,
                                ":max_turns": sessions.SESSION_MAX_TURNS,
                                ":max_bytes": sessions.SESSION_MAX_INPUT_BYTES,
                            }
                        ),
                    }
                },
            ]
        )
    except ClientError as exc:
        _raise_reserve_failure(exc, elder_id, conversation_id, session_id)
    return db.convert_decimals(item)


def _raise_reserve_failure(
    exc: ClientError, elder_id: str, conversation_id: str, session_id: str
) -> None:
    """區分「turn 已存在（重送）」與「session 不收了」，兩者的處置完全不同。"""
    if exc.response["Error"]["Code"] != "TransactionCanceledException":
        raise TurnError(f"reserve turn 失敗: {exc.response['Error']['Message']}")

    reasons = _cancellation_codes(exc)
    turn_exists = (
        reasons[0] == "ConditionalCheckFailed"
        if reasons
        else get_turn(elder_id, conversation_id) is not None
    )
    if turn_exists:
        # 兩個 invocation 同時處理同一個 client_request_id；輸掉的這個回頭走冪等判定
        raise TurnInProgressError("相同請求已被其他 invocation 接手")
    logger.info(
        "session 不接受新 turn，改用新 session：session_id=%s conversation_id=%s reasons=%s",
        session_id,
        conversation_id,
        reasons,
    )
    raise TurnReserveRejectedError("session 已不接受新的 turn")


def take_over(
    elder_id: str,
    conversation_id: str,
    *,
    owner: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """接管租約已到期的 `processing` turn。

    條件包含「仍是 processing 且租約仍是到期的」：前一個 invocation 其實還活著並剛續約，
    或已經 commit 成 completed 時，這次接管必須失敗，否則副作用會做第二次。
    不重新做 session routing 或 reserve——原本的 reservation 仍然有效。
    """
    moment = now or _now()
    table = db.get_dynamodb_resource().Table(db.TABLE_CONVERSATIONS)
    try:
        response = table.update_item(
            Key={"elder_id": elder_id, "record_id": turn_record_id(conversation_id)},
            UpdateExpression=(
                "SET request_lease_owner = :owner, request_lease_until = :lease_until"
            ),
            ConditionExpression=(
                "attribute_exists(record_id) AND request_status = :processing "
                "AND request_lease_until < :now"
            ),
            ExpressionAttributeValues={
                ":owner": owner,
                ":lease_until": format_ts(moment + timedelta(seconds=REQUEST_LEASE_SECONDS)),
                ":processing": STATUS_PROCESSING,
                ":now": format_ts(moment),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise TurnError(f"接管 turn 失敗: {exc.response['Error']['Message']}")
        raise TurnTransitionRejectedError("turn 已被接管或已進入終態")
    return db.convert_decimals(response.get("Attributes", {}))


# -----------------------------------------------------------------------------
# commit／fail：終態與 reservation 的解除
# -----------------------------------------------------------------------------


def commit(
    elder_id: str,
    conversation_id: str,
    *,
    session_id: str,
    owner: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """以單一 transaction 提交 `completed` 結果並解除 reservation。

    session 端的條件是「本 turn 是 inflight 佇列的頭」：`turn_ids` 必須按接納順序追加，
    後接納的 turn 先提交就會把順序寫反，而順序是 snapshot hash 的輸入。

    注意 realtime 的 routine／event 副作用目前由 Bedrock agent 的 tools Lambda 另外寫入，
    不在這個 transaction 裡；要做到「有回覆就一定有副作用」需要那些寫入回到本 process，
    併進同一個 TransactItems（規格見 docs/framework.md 的 final success 段落）。
    """
    moment = now or _now()
    completed_at = format_ts(moment)
    set_clause, turn_names, turn_values = _set_expression(
        {
            **result,
            "request_status": STATUS_COMPLETED,
            "system_status": "success",
            "ai_responded_at": completed_at,
        }
    )

    turn_values.update(
        db.to_attribute_values({":processing": STATUS_PROCESSING, ":owner": owner})
    )
    session_values = db.to_attribute_values(
        {
            ":cid": conversation_id,
            ":ids": [conversation_id],
            ":one": 1,
            ":bytes": input_bytes_of(result),
            ":now": completed_at,
            ":state_time": f"{completed_at}#{elder_id}#{session_id}",
            ":closed": sessions.STATE_CLOSED,
            ":empty": [],
        }
    )

    try:
        db.get_dynamodb_client().transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": db.TABLE_CONVERSATIONS,
                        "Key": db.to_attribute_values(
                            {"elder_id": elder_id, "record_id": turn_record_id(conversation_id)}
                        ),
                        "UpdateExpression": (
                            f"SET {set_clause} REMOVE request_lease_owner, request_lease_until"
                        ),
                        "ConditionExpression": (
                            "request_status = :processing AND request_lease_owner = :owner"
                        ),
                        "ExpressionAttributeNames": turn_names,
                        "ExpressionAttributeValues": turn_values,
                    }
                },
                {
                    "Update": {
                        "TableName": db.TABLE_CONVERSATIONS,
                        "Key": db.to_attribute_values(
                            {
                                "elder_id": elder_id,
                                "record_id": sessions.session_record_id(session_id),
                            }
                        ),
                        "UpdateExpression": (
                            "SET turn_ids = list_append(if_not_exists(turn_ids, :empty), :ids), "
                            "recent_conversation_ids = "
                            "list_append(if_not_exists(recent_conversation_ids, :empty), :ids), "
                            "turn_count = turn_count + :one, "
                            "input_bytes = input_bytes + :bytes, "
                            "inflight_turn_count = inflight_turn_count - :one, "
                            "last_activity_at = :now, session_state_time_key = :state_time "
                            "REMOVE inflight_turn_ids[0]"
                        ),
                        "ConditionExpression": (
                            "inflight_turn_ids[0] = :cid AND #state <> :closed"
                        ),
                        "ExpressionAttributeNames": {"#state": "state"},
                        "ExpressionAttributeValues": session_values,
                    }
                },
            ]
        )
    except ClientError as exc:
        _raise_transition_failure(exc, "提交 turn")
    return get_turn(elder_id, conversation_id) or {}


def fail(
    elder_id: str,
    conversation_id: str,
    *,
    session_id: str,
    code: str,
    message: str,
    http_status: int,
    owner: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把 turn 標成終態 `failed` 並解除 reservation。

    保存的是「穩定且安全化」的錯誤：同一個 client_request_id 之後重送要重播這個結果，
    因此訊息不得夾帶逐字稿或內部細節。失敗路徑保證沒有 routine／event 副作用，
    所以這裡不需要補償任何業務寫入，只要把名額還給 session。
    """
    moment = now or _now()
    condition = "attribute_exists(record_id) AND request_status = :processing"
    values: dict[str, Any] = {
        ":failed": STATUS_FAILED,
        ":failed_status": "failed",
        ":processing": STATUS_PROCESSING,
        ":code": code,
        ":message": message[:500],
        ":http_status": http_status,
        ":now": format_ts(moment),
    }
    if owner is not None:
        condition += " AND request_lease_owner = :owner"
        values[":owner"] = owner

    items: list[dict[str, Any]] = [
        {
            "Update": {
                "TableName": db.TABLE_CONVERSATIONS,
                "Key": db.to_attribute_values(
                    {"elder_id": elder_id, "record_id": turn_record_id(conversation_id)}
                ),
                "UpdateExpression": (
                    "SET request_status = :failed, system_status = :failed_status, "
                    "error_code = :code, error_message = :message, "
                    "error_http_status = :http_status, ai_responded_at = :now "
                    "REMOVE request_lease_owner, request_lease_until"
                ),
                "ConditionExpression": condition,
                "ExpressionAttributeValues": db.to_attribute_values(values),
            }
        }
    ]

    release = _release_reservation_item(elder_id, session_id, conversation_id)
    if release:
        items.append(release)

    try:
        db.get_dynamodb_client().transact_write_items(TransactItems=items)
    except ClientError as exc:
        _raise_transition_failure(exc, "標記 turn 失敗")
    return get_turn(elder_id, conversation_id) or {}


def release_reservation(elder_id: str, session_id: str, conversation_id: str) -> bool:
    """把已終態 turn 的 inflight 名額還給 session；名額本來就不在時回 False。

    供 closer 修復「turn 已終態、但 reservation 還掛著」的殘留狀態（例如 fail 的
    transaction 只成功了一半的極端情況）。
    """
    item = _release_reservation_item(elder_id, session_id, conversation_id)
    if item is None:
        return False
    try:
        db.get_dynamodb_client().transact_write_items(TransactItems=[item])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise TurnError(f"解除 reservation 失敗: {exc.response['Error']['Message']}")
        # 條件不成立代表已有人先修好了；重入是正常的
        return False
    return True


def _release_reservation_item(
    elder_id: str, session_id: str, conversation_id: str
) -> dict[str, Any] | None:
    """組出「從 inflight 清單移除本 turn」的 transaction item。

    DynamoDB 只能以索引移除 list 元素，因此先讀出位置，再以「該位置仍是這個 ID」為條件寫入；
    位置在讀寫之間被別人改掉時條件就不成立，不會誤刪別人的 reservation。
    """
    session = sessions.get_session(elder_id, session_id) or {}
    inflight = list(session.get("inflight_turn_ids") or [])
    if conversation_id not in inflight:
        return None
    index = inflight.index(conversation_id)
    return {
        "Update": {
            "TableName": db.TABLE_CONVERSATIONS,
            "Key": db.to_attribute_values(
                {"elder_id": elder_id, "record_id": sessions.session_record_id(session_id)}
            ),
            "UpdateExpression": (
                f"SET inflight_turn_count = inflight_turn_count - :one "
                f"REMOVE inflight_turn_ids[{index}]"
            ),
            "ConditionExpression": f"inflight_turn_ids[{index}] = :cid",
            "ExpressionAttributeValues": db.to_attribute_values(
                {":cid": conversation_id, ":one": 1}
            ),
        }
    }


def _set_expression(fields: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
    """把欄位字典組成 UpdateExpression 的 SET 子句（欄位名一律走 placeholder 避開保留字）。

    值為 None 的欄位直接略過而不寫成 DynamoDB 的 NULL：欄位不存在與「存在但是 null」對讀取端
    是兩件事，後者會讓 `turn.get(...)` 拿到 None 之後還得再判斷一次。
    """
    parts: list[str] = []
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for index, (field, value) in enumerate(
        sorted((item for item in fields.items() if item[1] is not None))
    ):
        parts.append(f"#f{index} = :f{index}")
        names[f"#f{index}"] = field
        values[f":f{index}"] = _serialize(value)
    return ", ".join(parts), names, values


def _serialize(value: Any) -> dict[str, Any]:
    return db.to_attribute_values({"v": db.prepare_item(value)})["v"]


def _cancellation_codes(exc: ClientError) -> list[str]:
    return [reason.get("Code", "None") for reason in exc.response.get("CancellationReasons", [])]


def _raise_transition_failure(exc: ClientError, action: str) -> None:
    code = exc.response["Error"]["Code"]
    if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
        raise TurnTransitionRejectedError(f"{action}的條件不成立：{_cancellation_codes(exc)}")
    raise TurnError(f"{action}失敗: {exc.response['Error']['Message']}")
