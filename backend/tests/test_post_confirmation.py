"""Cognito PostConfirmation trigger：不管收到什麼，都要把 event 原樣還回去。

這支掛在註冊流程上，Cognito 拿它的回傳值繼續走。**它拋錯或回錯東西，使用者的
註冊就失敗**——而註冊失敗的人根本進不了 App，也就無從回報。所以這裡盯的不是
它做了什麼，是它不會弄壞註冊。

原本它會把每個註冊完成的人自動訂閱到全域 SNS topic，包含長輩自己，於是長輩會
收到本來只該給照護者的警報。03a84c5 把那段拿掉，通知改由 tools Lambda 的
notify_caregiver 走 per-elder topic、只訂閱照護者。這份測試跟著改成盯現在的
契約：**這支不該再碰 SNS**。
"""
import pytest

from src.handlers import post_confirmation


def _event(trigger_source="PostConfirmation_ConfirmSignUp", **attrs):
    return {
        "triggerSource": trigger_source,
        "request": {"userAttributes": attrs},
    }


@pytest.mark.parametrize(
    "event",
    [
        _event(email="test@example.com"),
        # 同一支也會收到其他 trigger source
        _event("PostConfirmation_ConfirmForgotPassword", email="test@example.com"),
        # 沒有 email（只留手機，或屬性還沒寫進去）
        _event(phone_number="+886912345678"),
        # userAttributes 整個缺
        {"triggerSource": "PostConfirmation_ConfirmSignUp", "request": {}},
        # request 整個缺
        {"triggerSource": "PostConfirmation_ConfirmSignUp"},
        # 空 event：Cognito 不會這樣送，但這支絕不能因為讀不到欄位就炸
        {},
    ],
    ids=[
        "confirm_sign_up",
        "other_trigger_source",
        "no_email",
        "no_user_attributes",
        "no_request",
        "empty_event",
    ],
)
def test_returns_event_unchanged(event):
    """任何形狀的 event 都原樣回傳，且不拋錯——拋錯等於擋掉一個人的註冊。"""
    assert post_confirmation.handler(event, None) is event


def test_does_not_touch_aws(monkeypatch):
    """這支不該再有 SNS 那條路。

    盯行為而不是「模組裡沒有 get_sns_client 這個名字」：真正會出事的是有人把
    訂閱邏輯加回來，長輩又開始收到照護者的警報。這裡把 boto3 的 client 與
    resource 都換掉，一被呼叫就失敗。
    """
    import boto3

    def explode(*args, **kwargs):
        raise AssertionError(
            f"post_confirmation 不該建立 AWS client（收到 {args!r}）；"
            "照護者通知走 tools 的 notify_caregiver，見 03a84c5"
        )

    monkeypatch.setattr(boto3, "client", explode)
    monkeypatch.setattr(boto3, "resource", explode)

    event = _event(email="test@example.com")
    assert post_confirmation.handler(event, None) is event
