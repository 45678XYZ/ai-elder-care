"""對話輪次資料型別。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """單一對話輪次資料容器。"""

    conversation_id: str
    speaker: str
    text: str
    created_at: str
