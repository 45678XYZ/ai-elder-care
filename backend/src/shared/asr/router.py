"""
ASR Router — 固定 precedence 路由決策。

判定順序：language validity → route/CE-Formo production prohibition →
capability gate → cancellation → deadline → provider。

禁止依賴：handlers、HTTP、DB、AWS SDK。
"""
from __future__ import annotations

from .config import (
    AsrConfig,
    AwsCapabilityGate,
    CE_MODEL_METADATA,
    FORMO_MODEL_METADATA,
    UsageRestriction,
    make_route_not_approved_error,
    make_unsupported_language_error,
)
from .hak_mock import HakMockProvider
from .types import (
    AsrErrorCategory,
    AsrTerminalResult,
    CancellationSignal,
    CanonicalAudio,
    CorrelationContext,
    Deadline,
    Language,
    Transcript,
    TypedAsrError,
)


class AsrRouter:
    """
    ASR 路由器。

    依固定 precedence 決定路由結果：
    1. language validity — 未知 language 回 unsupported_language
    2. route/CE-Formo production prohibition — hak route 缺失/停用/非 mock 或
       CE/Formo production route 回 route_not_approved
    3. capability gate — zh-TW AWS gate 不完整回 route_not_approved
    4. cancellation — preflight cancellation 已觸發回 cancelled
    5. deadline — deadline 已到期回 deadline_exceeded
    6. provider — 呼叫對應 provider
    """

    def __init__(self, config: AsrConfig) -> None:
        self._config = config
        self._hak_mock_provider = HakMockProvider()

    def route(
        self,
        audio: CanonicalAudio,
        language: Language,
        deadline: Deadline,
        cancellation: CancellationSignal,
        context: CorrelationContext,
    ) -> AsrTerminalResult:
        """
        根據固定 precedence 決策路由。

        Returns:
            Transcript（成功）或 TypedAsrError（拒絕/錯誤）。
        """
        # ─── Step 1: Language validity ───
        if not isinstance(language, Language):
            return make_unsupported_language_error(str(language))

        # ─── Step 2: Route / CE-Formo production prohibition ───
        route_result = self._check_route(language)
        if route_result is not None:
            return route_result

        # ─── hak path — 已通過 route check，直接呼叫 HakMockProvider ───
        if language == Language.HAK:
            return self._hak_mock_provider.transcribe(
                audio, language, deadline, cancellation, context
            )

        # ─── zh-TW path ───
        # Step 3: Capability gate
        if not self._config.aws_capability_gate.is_complete:
            return make_route_not_approved_error(
                "AWS capability gate incomplete for zh-TW route."
            )

        # Step 4: Cancellation
        if cancellation.is_triggered:
            return TypedAsrError(
                category=AsrErrorCategory.CANCELLED,
                message="Cancelled before provider invocation.",
                retryable=False,
            )

        # Step 5: Deadline
        if deadline.is_expired():
            return TypedAsrError(
                category=AsrErrorCategory.DEADLINE_EXCEEDED,
                message="Deadline exceeded before provider invocation.",
                retryable=True,
            )

        # Step 6: Provider — zh-TW 需要 injected transport（本期不實作真實 transport）
        # 在完整實作中，這裡會呼叫 AWS adapter；目前回 route_not_approved
        # 因為本期沒有可用的 production transport。
        return make_route_not_approved_error(
            "zh-TW provider transport not available in current configuration."
        )

    def _check_route(self, language: Language) -> TypedAsrError | None:
        """
        檢查 route 與 CE/Formo production prohibition。

        Returns:
            None 表示通過；TypedAsrError 表示被拒絕。
        """
        lang_key = language.value

        # 檢查是否有對應 route config
        route_config = self._config.routes.get(lang_key)

        if language == Language.HAK:
            # hak route 必須存在、啟用、且 provider 為 hak_mock
            if route_config is None:
                return make_route_not_approved_error(
                    "No route configured for hak."
                )
            if not route_config.enabled:
                return make_route_not_approved_error(
                    "hak route is disabled."
                )
            if route_config.provider_identifier != "hak_mock":
                return make_route_not_approved_error(
                    "hak route provider is not hak_mock."
                )
            # 確認 provider config 存在且 hak_mock
            provider_config = self._config.providers.get(
                route_config.provider_identifier
            )
            if provider_config is None:
                return make_route_not_approved_error(
                    "hak_mock provider not found in configuration."
                )
            return None  # hak route 通過

        if language == Language.ZH_TW:
            # 檢查 CE/Formo production prohibition
            # 任何 route 關聯到 CE/Formo 且 metadata 為 production 都拒絕
            # （實際上 CE/Formo metadata 永遠是 colab_validation_only，
            #   但設計要求明確禁止 production invocation route）
            if route_config is not None:
                provider_id = route_config.provider_identifier
                provider_config = self._config.providers.get(provider_id)
                if provider_config is not None and provider_config.metadata_ref:
                    metadata = self._config.model_metadata.get(
                        provider_config.metadata_ref
                    )
                    if metadata is not None:
                        # CE 或 Formo 模型 — 無論 usage_restriction 為何都禁止 production
                        if metadata.model_id in (
                            CE_MODEL_METADATA.model_id,
                            FORMO_MODEL_METADATA.model_id,
                        ):
                            if metadata.usage_restriction != UsageRestriction.COLAB_VALIDATION_ONLY:
                                return make_route_not_approved_error(
                                    f"Production invocation of {metadata.model_id} is prohibited."
                                )
                            # colab_validation_only 也不允許 production route
                            return make_route_not_approved_error(
                                f"Route uses {metadata.model_id} which is restricted "
                                f"to colab validation only."
                            )

            if route_config is None:
                return make_route_not_approved_error(
                    "No route configured for zh-TW."
                )
            if not route_config.enabled:
                return make_route_not_approved_error(
                    "zh-TW route is disabled."
                )
            return None  # zh-TW route 通過基本檢查

        # 不應到達這裡（Language enum 只有 ZH_TW 和 HAK）
        return make_unsupported_language_error(lang_key)
