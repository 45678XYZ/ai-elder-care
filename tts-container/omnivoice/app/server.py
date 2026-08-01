"""uvicorn 進入點；設定不合法時在啟動當下就失敗，不讓 endpoint 半殘上線。"""

from __future__ import annotations

import logging

from .config import load_config
from .main import create_app
from .synthesizer import OmniVoiceSynthesizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = create_app(load_config(), OmniVoiceSynthesizer)
