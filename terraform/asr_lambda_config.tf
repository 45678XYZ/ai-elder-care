# ASR Lambda 設定注入與最小 IAM 權限
#
# 從 Terraform endpoint 名稱、allowed providers、routing 和 gates 組合
# ASR 設定 — Lambda 唯一的 ASR 設定來源。
#
# 本檔只負責組裝 JSON；實際傳遞由 lambda_config_parameters.tf 寫入 SSM，lambda.tf 只把
# 參數名稱交給 Chat Lambda（兩份設定相加超過 Lambda 4 KB 的環境變數上限）。即使 endpoint
# 關閉也明確組出 production-disabled 設定，避免空設定啟用 default_config() 的 hak_mock。

locals {
  # ASR_CONFIG_JSON — 由 Terraform 從 endpoint 名稱與設定組裝。
  # 啟用 SageMaker 端點時加入 CE／Formo；未啟用時中文仍使用 Amazon Transcribe，
  # 只有六條客語 production route 停用。
  # 建立 endpoint 不等於模型已核准；個別模型 ADR 未通過前，production gate
  # 必須維持關閉，build_provider_registry() 不會建立遠端 provider。
  # CE 未啟用時 fallback_chain 必須清空：留著 ce_remote 會讓 router 去呼叫一個不存在的
  # endpoint，把可重試的 provider 錯誤變成必然失敗。
  asr_ce_fallback_chain = local.asr_ce_enabled ? ["ce_remote"] : []

  asr_config_json = var.asr_enable_endpoints ? jsonencode({
    routes = merge({
      "zh-TW" = {
        route               = "zh_tw_primary"
        provider_identifier = "amazon_transcribe_zh_tw"
        enabled             = true
        fallback_chain      = local.asr_ce_fallback_chain
      }
      }, {
      for dialect, endpoint_name in local.asr_formo_endpoint_names :
      "hak:${dialect}" => {
        route               = "hak_${replace(dialect, "htia_", "")}_primary"
        provider_identifier = "formo_remote_${dialect}"
        enabled             = true
        fallback_chain      = local.asr_ce_fallback_chain
      }
    })
    providers = merge({
      amazon_transcribe_zh_tw = {
        identifier = "amazon_transcribe_zh_tw"
        status     = "enabled"
        kind       = "aws_managed"
      }
      }, local.asr_ce_enabled ? {
      ce_remote = {
        identifier    = "ce_remote"
        status        = "enabled"
        kind          = "remote_model"
        metadata_ref  = "taiwan_tongues_ce"
        endpoint_name = local.asr_ce_endpoint_name
      }
      } : {}, {
      for dialect, endpoint_name in local.asr_formo_endpoint_names :
      "formo_remote_${dialect}" => {
        identifier    = "formo_remote_${dialect}"
        status        = "enabled"
        kind          = "remote_model"
        metadata_ref  = "formospeech_whisper_v3"
        endpoint_name = endpoint_name
      }
    })
    model_metadata = merge(local.asr_ce_enabled ? {
      taiwan_tongues_ce = {
        model_id          = "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0"
        revision          = "v2.0"
        license           = "other"
        access_status     = "open"
        usage_restriction = "staging_validation_only"
        approval_state    = "not_approved"
        production_gate = {
          staging_validation_passed = false
          license_cleared           = false
          access_granted            = false
          quota_cleared             = false
          runtime_capacity_verified = false
          approval_record_ref       = null
        }
      }
      } : {}, {
      formospeech_whisper_v3 = {
        model_id      = "formospeech/whisper-large-v3-taiwanese-hakka"
        revision      = "main"
        license       = "CC BY-NC 4.0"
        access_status = "gated"
        # 三者同時成立，is_production_allowed 才為真；由單一變數控制避免只開一半。
        usage_restriction = var.asr_formo_approved ? "production" : "staging_validation_only"
        approval_state    = var.asr_formo_approved ? "approved" : "not_approved"
        production_gate = {
          staging_validation_passed = var.asr_formo_approved
          license_cleared           = var.asr_formo_approved
          # gated repo 的存取權已取得，與核准與否無關。
          access_granted            = true
          quota_cleared             = var.asr_formo_approved
          runtime_capacity_verified = var.asr_formo_approved
          approval_record_ref       = null
        }
      }
    })
    }) : jsonencode({
    routes = merge({
      "zh-TW" = {
        route               = "zh_tw_primary"
        provider_identifier = "amazon_transcribe_zh_tw"
        enabled             = true
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
      amazon_transcribe_zh_tw = {
        identifier = "amazon_transcribe_zh_tw"
        status     = "enabled"
        kind       = "aws_managed"
      }
      production_disabled = {
        identifier = "production_disabled"
        status     = "disabled"
        kind       = "mock"
      }
    }
    model_metadata = {}
  })
}
