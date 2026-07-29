"""統一環境變數組態管理 (Fail-Fast Config)。

將後端 Lambda 所需的全域環境變數統一由可升級單例管理，
在 Lambda 冷啟動 (Cold Start) 時完成讀取與型別驗證，避免執行途中因環境變數缺漏或格式錯誤拋出例外。
"""

from dataclasses import dataclass, field
import os


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes")


@dataclass(frozen=True)
class AppConfig:
    """後端全域基礎設施與服務設定檔。"""

    # DynamoDB 資料表名稱
    table_elders: str = "elders"
    table_conversations: str = "conversations"
    table_events: str = "events"
    table_daily_summaries: str = "daily_summaries"
    table_routines: str = "routines"
    table_elder_accounts: str = "elder-accounts"

    # SQS
    batch_queue_url: str = ""

    # Bedrock 對話與重試設定
    bedrock_model_id: str = "global.anthropic.claude-opus-4-6-v1:0"
    bedrock_max_attempts: int = 4
    bedrock_base_delay_seconds: float = 0.5
    bedrock_max_delay_seconds: float = 8.0

    # 運維與指標
    metrics_namespace: str = "ai-elder-care"
    metrics_enabled: bool = True

    @classmethod
    def from_env(cls) -> "AppConfig":
        """由環境變數讀取並建立快取單例。"""
        return cls(
            table_elders=_env_str("TABLE_ELDERS", "elders"),
            table_conversations=_env_str("TABLE_CONVERSATIONS", "conversations"),
            table_events=_env_str("TABLE_EVENTS", "events"),
            table_daily_summaries=_env_str("TABLE_DAILY_SUMMARIES", "daily_summaries"),
            table_routines=_env_str("TABLE_ROUTINES", "routines"),
            table_elder_accounts=_env_str("ELDER_ACCOUNTS_TABLE", "elder-accounts"),
            batch_queue_url=_env_str("BATCH_QUEUE_URL", ""),
            bedrock_model_id=_env_str("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1:0"),
            bedrock_max_attempts=_env_int("BEDROCK_MAX_ATTEMPTS", 4),
            bedrock_base_delay_seconds=float(_env_str("BEDROCK_BASE_DELAY_SECONDS", "0.5")),
            bedrock_max_delay_seconds=float(_env_str("BEDROCK_MAX_DELAY_SECONDS", "8.0")),
            metrics_namespace=_env_str("METRICS_NAMESPACE", "ai-elder-care"),
            metrics_enabled=_env_bool("METRICS_ENABLED", True),
        )


# 單例快取：Lambda 進入點於冷啟動載入模組時解析一次
get_config = AppConfig.from_env
