# ASR Lambda 設定注入與最小 IAM 權限
#
# 從 Terraform endpoint 名稱、allowed providers、routing 和 gates 組合
# ASR_CONFIG_JSON — Lambda 唯一的 ASR 設定來源。
#
# chat Lambda 的完整定義（打包、部署、API Gateway 整合）仍為 TODO，
# 本檔先定義：
#   1. ASR_CONFIG_JSON 的組裝邏輯（local value）
#   2. 呼叫端 IAM policy（已在 asr_models.tf 定義，此處說明 attach 方式）
#
# 部署者建立 chat Lambda 後需要：
#   - 設定環境變數 ASR_CONFIG_JSON = local.asr_config_json
#   - Attach aws_iam_policy.invoke_asr_endpoints 到 Lambda execution role

locals {
  # ASR_CONFIG_JSON — 由 Terraform 從 endpoint 名稱與設定組裝。
  # 啟用時產生完整的 remote-only 設定；未啟用時產生安全預設（僅 hak_mock 可用）。
  asr_config_json = var.asr_enable_endpoints ? jsonencode({
    routes = {
      "hak" = {
        route               = "hak_primary"
        provider_identifier = "hak_mock"
        enabled             = true
        fallback_chain      = ["ce_remote"]
      }
      "zh-TW" = {
        route               = "zh_tw_primary"
        provider_identifier = "ce_remote"
        enabled             = true
        fallback_chain      = ["formo_remote"]
      }
    }
    providers = {
      hak_mock = {
        identifier = "hak_mock"
        status     = "enabled"
        kind       = "mock"
      }
      ce_remote = {
        identifier     = "ce_remote"
        status         = "enabled"
        kind           = "remote_model"
        metadata_ref   = "taiwan_tongues_ce"
        endpoint_name  = local.asr_ce_endpoint_name
        max_concurrent = 4
      }
      formo_remote = {
        identifier     = "formo_remote"
        status         = "enabled"
        kind           = "remote_model"
        metadata_ref   = "formospeech_whisper_v3"
        endpoint_name  = local.asr_formo_endpoint_name
        max_concurrent = 2
      }
    }
    model_metadata = {
      taiwan_tongues_ce = {
        model_id          = "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0"
        revision          = "v2.0"
        license           = "other"
        access_status     = "open"
        usage_restriction = "production"
        approval_state    = "approved"
        production_gate = {
          colab_validation_passed    = true
          license_cleared            = true
          access_granted             = true
          quota_cleared              = true
          runtime_capacity_verified  = true
          approval_record_ref        = "docs/adr/asr-model-validation.md"
        }
      }
      formospeech_whisper_v3 = {
        model_id          = "formospeech/whisper-large-v3-taiwanese-hakka"
        revision          = "main"
        license           = "CC BY-NC 4.0"
        access_status     = "open"
        usage_restriction = "production"
        approval_state    = "approved"
        production_gate = {
          colab_validation_passed    = true
          license_cleared            = true
          access_granted             = true
          quota_cleared              = true
          runtime_capacity_verified  = true
          approval_record_ref        = "docs/adr/asr-model-validation.md"
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
  }) : ""  # 未啟用時不注入 — Lambda 會使用 default_config()（僅 hak_mock）
}
