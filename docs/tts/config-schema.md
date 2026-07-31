# TTS_CONFIG_JSON schema v1

`TTS_CONFIG_JSON` 是 Chat Lambda 唯一的 TTS 設定來源。缺少環境變數時使用完全停用設定；
JSON 無效、schema 不明、route 參照不存在 provider 或 remote model gate 不完整時 fail closed。

```json
{
  "schema_version": 1,
  "max_text_chars": 3000,
  "max_audio_bytes": 10485760,
  "routes": {
    "zh-TW": {
      "route": "zh_tw_traditional",
      "enabled": true,
      "provider_identifier": "polly_zhiyu_neural",
      "fallback_chain": ["polly_zhiyu_standard"]
    },
    "hak:htia_sixian": {
      "route": "hak_sixian",
      "enabled": true,
      "provider_identifier": "omnivoice_remote",
      "fallback_chain": ["voxhakka_remote"]
    }
  },
  "providers": {
    "omnivoice_remote": {
      "kind": "remote_model",
      "status": "enabled",
      "languages": ["hak"],
      "dialects": ["htia_sixian"],
      "metadata_ref": "omnivoice",
      "endpoint_name": "ai-elder-care-tts-omnivoice"
    }
  },
  "model_metadata": {
    "omnivoice": {
      "model_id": "formospeech/omnivoice-hakka-community-1",
      "revision": "main",
      "license": "CC BY-NC 4.0",
      "approved_for_production": false,
      "production_gate": {
        "staging_validation_passed": false,
        "license_cleared": false,
        "access_granted": false,
        "quota_cleared": false,
        "runtime_capacity_verified": false,
        "latency_slo_verified": false,
        "approval_record_ref": null
      }
    }
  }
}
```

`kind` 只允許 `mock|aws_managed|remote_model`。AWS provider 必須指定 `voice_id` 與
`neural|standard` engine；remote provider 必須指定 `metadata_ref` 與 `endpoint_name`。
遠端模型只有六個 gate 全為 true 且 `approved_for_production=true` 才會被 composition root
建立。程式另外只允許已登記的 OmniVoice、VoxHakka 與 BreezyVoice model ID。

Router 再檢查 provider 的 `languages` 與 `dialects`，因此 fallback_chain 即使設定錯誤也
不能跨語言或把南四縣送進 VoxHakka。
