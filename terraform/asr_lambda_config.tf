# ASR Lambda 設定注入與最小 IAM 權限
#
# 從 Terraform endpoint 名稱、allowed providers、routing 和 gates 組合
# ASR_CONFIG_JSON — Lambda 唯一的 ASR 設定來源。
#
# Chat Lambda 由 lambda.tf 注入本檔組裝的 JSON；即使 endpoint 關閉也明確注入
# production-disabled 設定，避免空環境變數啟用 default_config() 的 hak_mock。

locals {
  # ASR_CONFIG_JSON — 由 Terraform 從 endpoint 名稱與設定組裝。
  # 啟用時產生完整的 remote-only 設定；未啟用時兩條 production route 都停用。
  # 建立 endpoint 不等於模型已核准；個別模型 ADR 未通過前，production gate
  # 必須維持關閉，build_provider_registry() 不會建立遠端 provider。
  asr_config_json = var.asr_enable_endpoints ? jsonencode({
    routes = merge({
      "zh-TW" = {
        route               = "zh_tw_primary"
        provider_identifier = "ce_remote"
        enabled             = true
        fallback_chain      = []
      }
      }, {
      for dialect, endpoint_name in local.asr_formo_endpoint_names :
      "hak:${dialect}" => {
        route               = "hak_${replace(dialect, "htia_", "")}_primary"
        provider_identifier = "formo_remote_${dialect}"
        enabled             = true
        fallback_chain      = ["ce_remote"]
      }
    })
    providers = merge({
      ce_remote = {
        identifier     = "ce_remote"
        status         = "enabled"
        kind           = "remote_model"
        metadata_ref   = "taiwan_tongues_ce"
        endpoint_name  = local.asr_ce_endpoint_name
        max_concurrent = 4
      }
      }, {
      for dialect, endpoint_name in local.asr_formo_endpoint_names :
      "formo_remote_${dialect}" => {
        identifier     = "formo_remote_${dialect}"
        status         = "enabled"
        kind           = "remote_model"
        metadata_ref   = "formospeech_whisper_v3"
        endpoint_name  = endpoint_name
        max_concurrent = 2
      }
    })
    model_metadata = {
      taiwan_tongues_ce = {
        model_id          = "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0"
        revision          = "v2.0"
        license           = "other"
        access_status     = "open"
        usage_restriction = "colab_validation_only"
        approval_state    = "not_approved"
        production_gate = {
          colab_validation_passed   = false
          license_cleared           = false
          access_granted            = false
          quota_cleared             = false
          runtime_capacity_verified = false
          approval_record_ref       = null
        }
      }
      formospeech_whisper_v3 = {
        model_id          = "formospeech/whisper-large-v3-taiwanese-hakka"
        revision          = "main"
        license           = "CC BY-NC 4.0"
        access_status     = "gated"
        usage_restriction = "colab_validation_only"
        approval_state    = "not_approved"
        production_gate = {
          colab_validation_passed   = false
          license_cleared           = false
          access_granted            = false
          quota_cleared             = false
          runtime_capacity_verified = false
          approval_record_ref       = null
        }
      }
    }
    formo_prompt_id_allowlist = [
      "htia_sixian",
      "htia_hailu",
      "htia_dapu",
      "htia_raoping",
      "htia_zhaoan",
      "htia_nansixian",
    ]
    concurrency = {
      spill_wait_ms = 250
    }
    }) : jsonencode({
    routes = merge({
      "zh-TW" = {
        route               = "zh_tw_disabled"
        provider_identifier = "production_disabled"
        enabled             = false
        fallback_chain      = []
      }
      }, {
      for dialect in local.asr_formo_dialects :
      "hak:${dialect}" => {
        route               = "hak_${replace(dialect, "htia_", "")}_disabled"
        provider_identifier = "production_disabled"
        enabled             = false
        fallback_chain      = []
      }
    })
    providers = {
      production_disabled = {
        identifier = "production_disabled"
        status     = "disabled"
        kind       = "mock"
      }
    }
    model_metadata = {}
    formo_prompt_id_allowlist = [
      "htia_sixian",
      "htia_hailu",
      "htia_dapu",
      "htia_raoping",
      "htia_zhaoan",
      "htia_nansixian",
    ]
    concurrency = {
      spill_wait_ms = 250
    }
  })
}
