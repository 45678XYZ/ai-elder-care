"""request 契約驗證。

刻意不依賴 fastapi 或 torch：契約規則是這個容器最需要被測到的部分，抽開後測試不必
安裝整套推論依賴就能跑。契約定義見 docs/asr/sagemaker-inference-contract.md。
"""

from __future__ import annotations

from .config import (
    BYTES_PER_SAMPLE,
    MAX_AUDIO_BYTES,
    REQUIRED_CHANNELS,
    REQUIRED_SAMPLE_RATE_HZ,
    ContainerConfig,
)


class ContractError(ValueError):
    """request 不合契約；`code` 是回給呼叫端的穩定代碼，不含任何輸入內容。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_custom_attributes(raw: str | None) -> dict[str, str]:
    """解析 `language=hak;sample_rate_hz=16000;channels=1`。

    SageMaker 以 `X-Amzn-SageMaker-Custom-Attributes` header 傳遞。格式錯誤一律
    當成契約違反，不做寬鬆解讀——寬鬆解讀會讓錯誤設定悄悄跑到推論。
    """
    if not raw:
        raise ContractError("missing_custom_attributes")

    attributes: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        key, separator, value = part.partition("=")
        if not separator or not key.strip():
            raise ContractError("malformed_custom_attributes")
        attributes[key.strip()] = value.strip()
    if not attributes:
        raise ContractError("malformed_custom_attributes")
    return attributes


def validate_request(
    body: bytes, custom_attributes: str | None, config: ContainerConfig
) -> str:
    """驗證音訊與 CustomAttributes，回傳這次 request 的語言。"""
    attributes = parse_custom_attributes(custom_attributes)

    language = attributes.get("language", "")
    if language not in config.languages:
        raise ContractError("unsupported_language")

    if attributes.get("sample_rate_hz") != str(REQUIRED_SAMPLE_RATE_HZ):
        raise ContractError("unsupported_sample_rate")
    if attributes.get("channels") != str(REQUIRED_CHANNELS):
        raise ContractError("unsupported_channel_count")

    if not body:
        raise ContractError("empty_audio")
    if len(body) > MAX_AUDIO_BYTES:
        raise ContractError("audio_too_long")
    if len(body) % BYTES_PER_SAMPLE:
        # S16LE 每個 sample 兩個 byte；長度為奇數代表 body 被截斷或不是 PCM。
        raise ContractError("malformed_audio")

    return language
