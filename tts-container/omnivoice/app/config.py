"""容器啟動時固定的推論設定。

依 docs/tts/sagemaker-inference-contract.md，模型 ID／revision、支援語言、腔調與
default speaker 都必須在啟動時決定，不得由 request 改寫。任何缺漏都在啟動當下丟出
例外，而不是等到第一個 request 才壞。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# SageMaker 會把 model_data_url 的 tar.gz 解壓到這個路徑。
DEFAULT_MODEL_ROOT = Path("/opt/ml/model")

# OmniVoice 固定以 24 kHz 產生波形，見上游模型卡的 sf.write 範例。
OMNIVOICE_SAMPLE_RATE = 24000

# 六腔的 wire value 對應上游 `instruct` 參數要的中文腔調名稱。這份對照是部署邊界的
# 一部分：wire value 由 docs/asr/model-catalog.md 固定，instruct 字串由模型決定，
# 兩邊都不可由 request 覆寫。
DIALECT_INSTRUCTIONS = {
    "htia_sixian": "客語四縣腔",
    "htia_hailu": "客語海陸腔",
    "htia_dapu": "客語大埔腔",
    "htia_raoping": "客語饒平腔",
    "htia_zhaoan": "客語詔安腔",
    "htia_nansixian": "客語南四縣腔",
}


class ConfigError(RuntimeError):
    """容器設定不完整；只在啟動階段丟出。"""


@dataclass(frozen=True)
class ContainerConfig:
    model_id: str
    model_revision: str
    languages: frozenset[str]
    dialects: frozenset[str]
    default_speaker: str
    model_root: Path
    max_text_chars: int

    @property
    def weights_dir(self) -> Path:
        return self.model_root / "omnivoice"

    @property
    def speakers_dir(self) -> Path:
        return self.model_root / "speakers"


def _split_csv(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def load_config(env: dict[str, str] | None = None) -> ContainerConfig:
    """從環境變數組出設定；terraform 的 aws_sagemaker_model 負責注入這些鍵。"""
    source = os.environ if env is None else env

    model_id = source.get("TTS_MODEL_ID", "").strip()
    if not model_id:
        raise ConfigError("TTS_MODEL_ID is required.")

    languages = _split_csv(source.get("TTS_LANGUAGES", ""))
    if not languages:
        raise ConfigError("TTS_LANGUAGES is required.")

    dialects = _split_csv(source.get("TTS_DIALECTS", ""))
    if not dialects:
        raise ConfigError("TTS_DIALECTS is required for the hakka route.")
    unknown = dialects - DIALECT_INSTRUCTIONS.keys()
    if unknown:
        # 不把未知腔調寫進訊息，避免部署設定錯誤時把任意字串帶進 log。
        raise ConfigError("TTS_DIALECTS contains values this container cannot serve.")

    try:
        max_text_chars = int(source.get("TTS_MAX_TEXT_CHARS", "3000"))
    except ValueError as exc:
        raise ConfigError("TTS_MAX_TEXT_CHARS must be an integer.") from exc
    if max_text_chars <= 0:
        raise ConfigError("TTS_MAX_TEXT_CHARS must be positive.")

    return ContainerConfig(
        model_id=model_id,
        model_revision=source.get("TTS_MODEL_REVISION", "main").strip() or "main",
        languages=languages,
        dialects=dialects,
        default_speaker=source.get("TTS_DEFAULT_SPEAKER", "").strip(),
        model_root=Path(source.get("TTS_MODEL_ROOT", str(DEFAULT_MODEL_ROOT))),
        max_text_chars=max_text_chars,
    )


def resolve_speaker_dir(config: ContainerConfig, speaker: str | None) -> Path:
    """把 request 的 speaker 對應到 artifact 內已打包的聲紋目錄。

    OmniVoice 與 BreezyVoice 一樣是 zero-shot：音色來自參考音檔，所以「有哪些聲音
    可用」等於「artifact 裡打包了哪些聲紋」。只接受已存在的目錄名，request 不能
    指定任意路徑。
    """
    name = (speaker or config.default_speaker or "default").strip()
    if not name or "/" in name or name.startswith("."):
        raise ValueError("invalid speaker")
    speaker_dir = config.speakers_dir / name
    if not (speaker_dir / "prompt.wav").is_file():
        raise ValueError("unknown speaker")
    return speaker_dir
