"""request 契約驗證。

刻意不依賴 fastapi：契約規則是這個容器最需要被測到的部分，抽開後測試不必安裝
整套 serving 依賴就能跑。契約定義見 docs/tts/sagemaker-inference-contract.md。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import ContainerConfig

# 契約只允許 MP3；容器不提供其他輸出格式，避免 Lambda 端出現未預期的 content type。
SUPPORTED_FORMAT = "mp3"


class ContractError(ValueError):
    """request 不合契約；`code` 是回給呼叫端的穩定代碼，不含任何輸入內容。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    speaker: str | None


def parse_payload(raw: bytes, config: ContainerConfig) -> SynthesisRequest:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("malformed_body") from exc
    if not isinstance(payload, dict):
        raise ContractError("malformed_body")

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ContractError("invalid_text")
    if len(text) > config.max_text_chars:
        raise ContractError("text_too_long")

    language = payload.get("language")
    if not isinstance(language, str) or language not in config.languages:
        raise ContractError("unsupported_language")

    if payload.get("format", SUPPORTED_FORMAT) != SUPPORTED_FORMAT:
        raise ContractError("unsupported_format")

    dialect = payload.get("dialect")
    if dialect is not None:
        # 台灣華語沒有腔調維度，收到 dialect 代表路由走錯，必須擋掉而不是默默忽略。
        if not isinstance(dialect, str) or dialect not in config.dialects:
            raise ContractError("unsupported_dialect")

    speaker = payload.get("speaker")
    if speaker is not None and not isinstance(speaker, str):
        raise ContractError("invalid_speaker")

    return SynthesisRequest(text=text, speaker=speaker)
