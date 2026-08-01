"""容器啟動時固定的推論設定。

依 docs/asr/sagemaker-inference-contract.md，模型 ID／revision、支援語言、prompt ID
與 generation language 都在部署期固定，request 不得攜帶也不得覆寫。任何缺漏都在啟動
當下丟出例外，而不是等到第一個 request 才壞。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# SageMaker 會把 model_data_url 的 tar.gz 解壓到這個路徑。
DEFAULT_MODEL_ROOT = Path("/opt/ml/model")

# Lambda 的 canonical audio 固定規格；契約不允許其他組合。
REQUIRED_SAMPLE_RATE_HZ = 16000
REQUIRED_CHANNELS = 1

# 16-bit little-endian、單聲道、16 kHz，最長 60 秒。
BYTES_PER_SAMPLE = 2
MAX_AUDIO_BYTES = REQUIRED_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * 60

# 六腔 wire value；FORMO_PROMPT_ID 必須是其中之一。
SUPPORTED_PROMPT_IDS = frozenset(
    {
        "htia_sixian",
        "htia_hailu",
        "htia_dapu",
        "htia_raoping",
        "htia_zhaoan",
        "htia_nansixian",
    }
)


class ConfigError(RuntimeError):
    """容器設定不完整；只在啟動階段丟出。"""


@dataclass(frozen=True)
class ContainerConfig:
    model_id: str
    model_revision: str
    languages: frozenset[str]
    prompt_id: str
    generation_language: str
    model_root: Path

    @property
    def weights_dir(self) -> Path:
        return self.model_root / "formospeech"


def _split_csv(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def load_config(env: dict[str, str] | None = None) -> ContainerConfig:
    """從環境變數組出設定；terraform 的 aws_sagemaker_model 負責注入這些鍵。"""
    source = os.environ if env is None else env

    model_id = source.get("ASR_MODEL_ID", "").strip()
    if not model_id:
        raise ConfigError("ASR_MODEL_ID is required.")

    languages = _split_csv(source.get("ASR_LANGUAGES", ""))
    if not languages:
        raise ConfigError("ASR_LANGUAGES is required.")

    prompt_id = source.get("FORMO_PROMPT_ID", "").strip()
    if prompt_id not in SUPPORTED_PROMPT_IDS:
        # 不回顯實際值：部署設定錯誤時不該把任意字串帶進啟動 log。
        raise ConfigError("FORMO_PROMPT_ID must be one of the six hakka wire values.")

    # 固定 Chinese 讓 Whisper 輸出客語漢字；這不是 zh-TW capability，見 model-catalog。
    generation_language = source.get("FORMO_GENERATION_LANGUAGE", "").strip()
    if not generation_language:
        raise ConfigError("FORMO_GENERATION_LANGUAGE is required.")

    return ContainerConfig(
        model_id=model_id,
        model_revision=source.get("ASR_MODEL_REVISION", "main").strip() or "main",
        languages=languages,
        prompt_id=prompt_id,
        generation_language=generation_language,
        model_root=Path(source.get("ASR_MODEL_ROOT", str(DEFAULT_MODEL_ROOT))),
    )
