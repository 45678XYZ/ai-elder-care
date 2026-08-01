# ASR_CONFIG_JSON 規格

`ASR_CONFIG_JSON` 是 Chat Lambda 唯一的 ASR 設定來源。未設定或空白時使用
`default_config()`（僅本機／測試 `hak_mock`）；有值但 JSON 或 schema 無效時拋出
`ConfigParseError`，不得退回預設值。AWS Region 是唯一不放在此 JSON 的執行設定；在
Lambda 由 runtime 自動注入 `AWS_REGION`，Terraform 不得重複設定該保留鍵。

Parser：`backend/src/shared/asr/config.py`；Terraform：
`terraform/asr_lambda_config.tf`。

## 最小結構

```json
{
  "routes": {
    "zh-TW": {
      "route": "zh_tw_primary",
      "provider_identifier": "amazon_transcribe_zh_tw",
      "enabled": true,
      "fallback_chain": ["ce_remote"]
    },
    "hak:htia_sixian": {
      "route": "hak_sixian_primary",
      "provider_identifier": "formo_remote_htia_sixian",
      "enabled": true,
      "fallback_chain": ["ce_remote"]
    }
  },
  "providers": {
    "amazon_transcribe_zh_tw": {
      "identifier": "amazon_transcribe_zh_tw",
      "status": "enabled",
      "kind": "aws_managed"
    },
    "ce_remote": {
      "identifier": "ce_remote",
      "status": "enabled",
      "kind": "remote_model",
      "metadata_ref": "taiwan_tongues_ce",
      "endpoint_name": "ai-elder-care-asr-ce"
    },
    "formo_remote_htia_sixian": {
      "identifier": "formo_remote_htia_sixian",
      "status": "enabled",
      "kind": "remote_model",
      "metadata_ref": "formospeech_whisper_v3",
      "endpoint_name": "ai-elder-care-asr-formo-sixian"
    }
  },
  "model_metadata": {
    "taiwan_tongues_ce": {
      "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
      "revision": "v2.0",
      "license": "other",
      "access_status": "open",
      "usage_restriction": "staging_validation_only",
      "approval_state": "not_approved",
      "production_gate": {
        "staging_validation_passed": false,
        "license_cleared": false,
        "access_granted": false,
        "quota_cleared": false,
        "runtime_capacity_verified": false,
        "approval_record_ref": null
      }
    }
  }
}
```

完整 Terraform 設定另含六個 `hak:<dialect>` routes 與六個固定 endpoint 的 Formo
providers；此處只列一腔以說明 schema。

## 欄位

### Routes

中文 key 為 `zh-TW`；客語 production key 為 `hak:<六腔值>`。generic `hak` 只供
`default_config()` mock 相容。

| 欄位 | 規則 |
|---|---|
| `route` | 非空白名稱，只供安全遙測識別 |
| `provider_identifier` | 主 provider，必須存在於 `providers` |
| `enabled` | boolean；false 時不外呼 |
| `fallback_chain` | 選填 provider ID list；不得包含主 provider、重複或未知 ID |

固定 provider 順序：

- `zh-TW`：`amazon_transcribe_zh_tw` → `ce_remote`
- `hak:<六腔>`：對應的 `formo_remote_<dialect>` → `ce_remote`

### Providers

| 欄位 | 規則 |
|---|---|
| `identifier` | 必填非空白 ID；Terraform 使它與 map key 相同 |
| `status` | `enabled`、`disabled` 或 `staging_only` |
| `kind` | `mock`、`aws_managed` 或 `remote_model` |
| `metadata_ref` | remote model 必填，指向 `model_metadata` |
| `endpoint_name` | remote model 必填；不得用於 managed provider |

`aws_managed` 目前只允許精確 ID `amazon_transcribe_zh_tw`，且最小設定只能包含
`identifier`、`status`、`kind`。Composition root 與 provider 內部固定 Amazon Transcribe
Streaming、`zh-TW`、16 kHz、PCM；不得從 JSON 自訂 service、language code、sample rate、
encoding 或任意 AWS managed provider。

Lambda 不管理程序內模型槽、併發數或等待佇列。Transcribe service capacity 由 AWS 管理；
SageMaker 容量由 endpoint 設定負責，因此 JSON 沒有 `max_concurrent` 或 `concurrency`。

### Model metadata 與 production gate

`access_status` 為 `open|gated|restricted`；`usage_restriction` 為
`staging_validation_only|production`；`approval_state` 為
`not_approved|approved|pending`。

遠端模型只有以下條件全部成立才可建立 production provider：

- `usage_restriction=production`
- `approval_state=approved`
- `staging_validation_passed`、`license_cleared`、`access_granted`、
  `quota_cleared`、`runtime_capacity_verified` 全為 true

缺少 gate 欄位視為 false。`approval_record_ref` 指向逐模型 ADR 或為 null。固定模型事實與
目前狀態見 [`model-catalog.md`](model-catalog.md)。`aws_managed` 不使用自託管模型 gate，
但仍受精確 provider allowlist、IAM、route capability、deadline 與 typed error 約束。

六腔值由 Chat profile 的 `HakkaDialect` 型別約束；Lambda 只以它選擇 route，且不得把
Formo prompt ID 或 `FORMO_GENERATION_LANGUAGE` 放入 `ASR_CONFIG_JSON`／SageMaker request。
這兩者都固定在各 endpoint 的部署環境。

## Fail-closed 驗證

以下任一情況拒絕整份設定或使對應 route 不可用：

- 缺少頂層 `routes`、`providers` 或 `model_metadata`。
- route 參照未知、重複或能力不相容的 provider。
- managed provider 不是精確 ID `amazon_transcribe_zh_tw`，或帶入不允許欄位。
- remote provider 缺 `metadata_ref` 或 `endpoint_name`。
- 宣告 production 但 approval state 或五項 gate 不完整。
- Formo 被登記到 `zh-TW`，或 CE/Formo 在未核准時被外呼。
- enum、boolean 或 collection 型別不符。

## Terraform 行為

- `asr_enable_endpoints=false`：不建立 CE/Formo endpoints；中文仍可使用受控 Transcribe，
  所有客語 production routes fail closed，且部署環境不啟用 mock。
- `asr_enable_endpoints=true`：建立一個 CE 與六個 Formo endpoints，並填入 routes；模型仍須
  獨立通過核准才能由 composition 建立 provider。
- 建立 endpoint 不等於核准模型；未核准 provider 不會外呼，也不會成為 fallback。
