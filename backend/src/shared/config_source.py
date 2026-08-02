"""ASR／TTS 設定的來源解析：環境變數優先，容不下時改由 SSM Parameter Store 取得。

Lambda 的環境變數總量上限是 4 KB（所有 key 與 value 相加）。六腔客語 ASR 全開時
`ASR_CONFIG_JSON` 約 2.6 KB、雙 TTS endpoint 的 `TTS_CONFIG_JSON` 約 2.5 KB，兩者相加
就超過上限——這不是可以靠精簡欄位繞過的規模問題，因此設定改為可放在 SSM，環境變數只留
參數名稱。

環境變數仍然優先，理由是本機開發、單元測試與 endpoint 全關的小設定都不必動用 SSM，
既有以 `ASR_CONFIG_JSON`／`TTS_CONFIG_JSON` 注入的測試也完全不受影響。

**取得失敗一律 fail closed**：SSM 讀不到時拋 `ConfigSourceError`，絕不可以退回各模組的
預設設定。ASR 的 `default_config()` 會啟用 `hak_mock`、TTS 的 `disabled_config()` 會讓
語音靜默——把一次暫時性的 SSM 故障變成「安靜地換掉辨識與發聲行為」，比直接失敗危險得多。
"""

from __future__ import annotations

import os
import threading

import boto3

ENV_AWS_REGION = "AWS_REGION"


class ConfigSourceError(RuntimeError):
    """設定來源無法取得；呼叫端必須 fail closed，不得退回預設值。"""


_ssm_client = None
_client_lock = threading.Lock()


def _client():
    global _ssm_client
    if _ssm_client is None:
        with _client_lock:
            if _ssm_client is None:
                _ssm_client = boto3.client(
                    "ssm", region_name=os.environ.get(ENV_AWS_REGION) or None
                )
    return _ssm_client


def load_raw_config(json_env: str, parameter_env: str) -> str | None:
    """依序嘗試環境變數與 SSM 參數，兩者都沒設定時回傳 None 讓呼叫端套用預設。

    只有「兩個環境變數都沒設」才回傳 None。指定了參數名稱卻讀不到，代表部署意圖是走
    SSM 而基礎設施出了問題，必須拋錯。
    """
    raw = os.environ.get(json_env)
    if raw and raw.strip():
        return raw

    name = os.environ.get(parameter_env)
    if not name or not name.strip():
        return None

    try:
        response = _client().get_parameter(Name=name.strip(), WithDecryption=True)
        value = response["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 — 任何取得失敗都必須 fail closed
        raise ConfigSourceError(
            f"failed to read config from SSM parameter in {parameter_env}. Fail closed."
        ) from exc

    if not value or not value.strip():
        raise ConfigSourceError(
            f"SSM parameter in {parameter_env} is empty. Fail closed."
        )
    return value
