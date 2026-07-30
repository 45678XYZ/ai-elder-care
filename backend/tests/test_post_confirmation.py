import pytest
from unittest.mock import MagicMock
from src.handlers import post_confirmation

@pytest.fixture
def mock_sns(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(post_confirmation, "get_sns_client", lambda: mock_client)
    monkeypatch.setattr(post_confirmation, "CAREGIVER_NOTIFY_TOPIC_ARN", "arn:aws:sns:dummy-topic")
    return mock_client

def test_post_confirmation_success(mock_sns):
    """測試正常情況下，能成功抓取 email 並呼叫 sns.subscribe"""
    event = {
        "triggerSource": "PostConfirmation_ConfirmSignUp",
        "request": {
            "userAttributes": {
                "email": "test@example.com"
            }
        }
    }
    result = post_confirmation.handler(event, None)
    
    # 確保回傳完整的 event
    assert result == event
    
    # 確保正確呼叫 subscribe
    mock_sns.subscribe.assert_called_once_with(
        TopicArn="arn:aws:sns:dummy-topic",
        Protocol="email",
        Endpoint="test@example.com",
        ReturnSubscriptionArn=True
    )

def test_post_confirmation_wrong_trigger_source(mock_sns):
    """測試非 ConfirmSignUp 事件應直接忽略"""
    event = {
        "triggerSource": "PreSignUp_AdminCreateUser",
        "request": {
            "userAttributes": {
                "email": "test@example.com"
            }
        }
    }
    result = post_confirmation.handler(event, None)
    assert result == event
    mock_sns.subscribe.assert_not_called()

def test_post_confirmation_missing_email(mock_sns):
    """測試 userAttributes 沒有 email 的情況"""
    event = {
        "triggerSource": "PostConfirmation_ConfirmSignUp",
        "request": {
            "userAttributes": {
                "phone_number": "+1234567890"
            }
        }
    }
    result = post_confirmation.handler(event, None)
    assert result == event
    mock_sns.subscribe.assert_not_called()

def test_post_confirmation_exception_handled(mock_sns):
    """測試 SNS 發生錯誤時，不會拋出異常而影響 Cognito 流程"""
    mock_sns.subscribe.side_effect = Exception("AWS SNS Error")
    
    event = {
        "triggerSource": "PostConfirmation_ConfirmSignUp",
        "request": {
            "userAttributes": {
                "email": "test@example.com"
            }
        }
    }
    
    # 執行不應該拋出錯誤
    result = post_confirmation.handler(event, None)
    assert result == event
    mock_sns.subscribe.assert_called_once()
