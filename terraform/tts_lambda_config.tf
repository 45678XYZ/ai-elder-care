# 這份設定是 Chat Lambda 唯一的 TTS provider／route 設定來源。實際傳遞由
# lambda_config_parameters.tf 寫入 SSM，Lambda 只拿到參數名稱。

locals {
  tts_approval = {
    omnivoice   = var.tts_omnivoice_approved
    voxhakka    = var.tts_voxhakka_approved
    breezyvoice = var.tts_breezyvoice_approved
  }

  tts_config_json = jsonencode({
    schema_version  = 1
    max_text_chars  = 3000
    max_audio_bytes = 10485760

    routes = merge({
      "zh-TW" = {
        route               = "zh_tw_traditional"
        enabled             = true
        provider_identifier = var.tts_enable_breezyvoice_endpoint ? "breezyvoice_remote" : "polly_zhiyu_neural"
        # BreezyVoice 核准後優先台灣華語；Zhiyu 為 cmn-CN 相容備援，不宣稱台灣口音。
        fallback_chain = var.tts_enable_breezyvoice_endpoint ? [
          "polly_zhiyu_neural",
          "polly_zhiyu_standard",
        ] : ["polly_zhiyu_standard"]
      }
      }, {
      for dialect in local.tts_hakka_dialects :
      "hak:${dialect}" => {
        route = "hak_${replace(dialect, "htia_", "")}"
        enabled = var.tts_enable_omnivoice_endpoint || (
          var.tts_enable_voxhakka_endpoint && contains(local.tts_voxhakka_dialects, dialect)
        )
        provider_identifier = var.tts_enable_omnivoice_endpoint ? "omnivoice_remote" : (
          var.tts_enable_voxhakka_endpoint && contains(local.tts_voxhakka_dialects, dialect)
          ? "voxhakka_remote"
          : "production_disabled"
        )
        fallback_chain = (
          var.tts_enable_omnivoice_endpoint &&
          var.tts_enable_voxhakka_endpoint &&
          contains(local.tts_voxhakka_dialects, dialect)
        ) ? ["voxhakka_remote"] : []
      }
    })

    providers = merge({
      production_disabled = {
        kind      = "mock"
        status    = "disabled"
        languages = ["zh-TW", "hak"]
        dialects  = []
      }
      polly_zhiyu_neural = {
        kind      = "aws_managed"
        status    = "enabled"
        languages = ["zh-TW"]
        dialects  = []
        voice_id  = "Zhiyu"
        engine    = "neural"
      }
      polly_zhiyu_standard = {
        kind      = "aws_managed"
        status    = "enabled"
        languages = ["zh-TW"]
        dialects  = []
        voice_id  = "Zhiyu"
        engine    = "standard"
      }
      }, var.tts_enable_omnivoice_endpoint ? {
      omnivoice_remote = {
        kind          = "remote_model"
        status        = "enabled"
        languages     = ["hak"]
        dialects      = sort(tolist(local.tts_hakka_dialects))
        metadata_ref  = "omnivoice"
        endpoint_name = local.tts_remote_models.omnivoice.endpoint_name
      }
      } : {}, var.tts_enable_voxhakka_endpoint ? {
      voxhakka_remote = {
        kind          = "remote_model"
        status        = "enabled"
        languages     = ["hak"]
        dialects      = sort(tolist(local.tts_voxhakka_dialects))
        metadata_ref  = "voxhakka"
        endpoint_name = local.tts_remote_models.voxhakka.endpoint_name
        speaker       = "XF"
      }
      } : {}, var.tts_enable_breezyvoice_endpoint ? {
      breezyvoice_remote = {
        kind          = "remote_model"
        status        = "enabled"
        languages     = ["zh-TW"]
        dialects      = []
        metadata_ref  = "breezyvoice"
        endpoint_name = local.tts_remote_models.breezyvoice.endpoint_name
      }
    } : {})

    model_metadata = merge(
      var.tts_enable_omnivoice_endpoint ? {
        omnivoice = {
          model_id                = "formospeech/omnivoice-hakka-community-1"
          revision                = "main"
          license                 = "CC BY-NC 4.0"
          approved_for_production = local.tts_approval.omnivoice
          production_gate = {
            staging_validation_passed = local.tts_approval.omnivoice
            license_cleared           = local.tts_approval.omnivoice
            access_granted            = local.tts_approval.omnivoice
            quota_cleared             = local.tts_approval.omnivoice
            runtime_capacity_verified = local.tts_approval.omnivoice
            latency_slo_verified      = local.tts_approval.omnivoice
            approval_record_ref       = null
          }
        }
      } : {},
      var.tts_enable_voxhakka_endpoint ? {
        voxhakka = {
          model_id                = "formospeech/yourtts-htia-240704"
          revision                = "main"
          license                 = "CC BY-NC 4.0"
          approved_for_production = local.tts_approval.voxhakka
          production_gate = {
            staging_validation_passed = local.tts_approval.voxhakka
            license_cleared           = local.tts_approval.voxhakka
            access_granted            = local.tts_approval.voxhakka
            quota_cleared             = local.tts_approval.voxhakka
            runtime_capacity_verified = local.tts_approval.voxhakka
            latency_slo_verified      = local.tts_approval.voxhakka
            approval_record_ref       = null
          }
        }
      } : {},
      var.tts_enable_breezyvoice_endpoint ? {
        breezyvoice = {
          model_id                = "MediaTek-Research/BreezyVoice"
          revision                = "main"
          license                 = "Apache-2.0"
          approved_for_production = local.tts_approval.breezyvoice
          production_gate = {
            staging_validation_passed = local.tts_approval.breezyvoice
            license_cleared           = local.tts_approval.breezyvoice
            access_granted            = local.tts_approval.breezyvoice
            quota_cleared             = local.tts_approval.breezyvoice
            runtime_capacity_verified = local.tts_approval.breezyvoice
            latency_slo_verified      = local.tts_approval.breezyvoice
            approval_record_ref       = null
          }
        }
      } : {}
    )
  })
}
