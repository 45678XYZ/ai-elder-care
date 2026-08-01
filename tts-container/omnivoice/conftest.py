"""讓測試能以 `app.` 匯入 serving 套件，不必安裝成套件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
