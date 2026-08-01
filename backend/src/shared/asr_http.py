"""
ASR 領域結果 → 公開 HTTP 錯誤的對映。規格見 docs/api.md。

刻意放在 `shared/asr/` **之外**：ASR 領域套件不認識 HTTP、不知道錯誤碼，
這一層才是領域與公開契約的交界。

對映原則：

- 呼叫端能自行修正的問題回 4xx，且對映到 `docs/api.md` 已定義的錯誤碼。
- 其餘（未核准、逾時、provider 故障）對使用者而言都是「服務端的事」，
  一律 500 `INTERNAL_ERROR`。這讓公開契約不必因為後端換模型而變動。
- **回給呼叫端的訊息一律用固定文案**，不透出 `TypedAsrError.message`。
  後者是內部診斷用（可能含「AWS capability gate incomplete」這類佈署細節），
  只能寫進伺服器日誌。
"""
from __future__ import annotations

from typing import NamedTuple

from src.shared.asr.types import AsrErrorCategory


class PublicAsrError(NamedTuple):
    """對映後的公開錯誤：HTTP 狀態、穩定錯誤碼與給使用者看的訊息。"""

    status_code: int
    code: str
    message: str


# 每一個 AsrErrorCategory 都必須在此表中有對應項目；下方的完整性檢查會擋住漏項。
ASR_ERROR_HTTP_MAPPING: dict[AsrErrorCategory, PublicAsrError] = {
    # ── 呼叫端可修正 ──
    AsrErrorCategory.AUDIO_DURATION_EXCEEDED: PublicAsrError(
        400, "AUDIO_TOO_LONG", "單句語音長度超過 60 秒限制"
    ),
    AsrErrorCategory.INVALID_AUDIO: PublicAsrError(
        400, "INVALID_PARAMETER", "音訊內容無效或無法解碼"
    ),
    AsrErrorCategory.UNSUPPORTED_AUDIO_FORMAT: PublicAsrError(
        400, "INVALID_PARAMETER", "audio.format 僅支援 wav 或 m4a"
    ),
    AsrErrorCategory.UNSUPPORTED_LANGUAGE: PublicAsrError(
        400, "INVALID_PARAMETER", "lang 僅支援 zh-TW 或 hak"
    ),
    # ── 服務端問題：對使用者一律同一種說法 ──
    AsrErrorCategory.ROUTE_NOT_APPROVED: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識服務目前無法使用"
    ),
    AsrErrorCategory.DEADLINE_EXCEEDED: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識處理逾時"
    ),
    AsrErrorCategory.CANCELLED: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識已中止"
    ),
    AsrErrorCategory.PROVIDER_UNAVAILABLE: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識服務目前無法使用"
    ),
    AsrErrorCategory.PROVIDER_INVALID_RESPONSE: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識結果無效"
    ),
    AsrErrorCategory.PROVIDER_FAILURE: PublicAsrError(
        500, "INTERNAL_ERROR", "語音辨識失敗"
    ),
}

# 匯入時就檢查完整性：新增錯誤分類卻忘記對映，會在部署前就被發現，
# 而不是在執行期掉進 KeyError。
_MISSING = set(AsrErrorCategory) - set(ASR_ERROR_HTTP_MAPPING)
if _MISSING:
    raise RuntimeError(
        "ASR_ERROR_HTTP_MAPPING is missing categories: "
        f"{sorted(c.value for c in _MISSING)}"
    )

# 對映到 500 的分類：這些值得寫伺服器日誌，4xx 則是正常的呼叫端輸入問題。
SERVER_SIDE_CATEGORIES = frozenset(
    category
    for category, mapped in ASR_ERROR_HTTP_MAPPING.items()
    if mapped.status_code >= 500
)


def map_asr_error(category: AsrErrorCategory) -> PublicAsrError:
    """取得公開錯誤對映。未知分類同樣收斂為 500，不外洩細節。"""
    return ASR_ERROR_HTTP_MAPPING.get(
        category,
        PublicAsrError(500, "INTERNAL_ERROR", "語音辨識失敗"),
    )
