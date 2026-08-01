# ASR_CONFIG_JSON 規格

`ASR_CONFIG_JSON` 是 Chat Lambda 唯一的 ASR 設定來源。未設定或空白時使用
`default_config()`（僅本機／測試 `hak_mock`）；有值但 JSON 或 schema 無效時拋出
`ConfigParseError`，不得退回預設值。

Parser：`backend/src/shared/asr/config.py`；Terraform：
`terraform/asr_lambda_config.tf`。

## 最小結構

```json
{
  "routes": {
    "zh-TW": {
      "route": "zh_tw_primary",
      "provider_identifier": "ce_remote",
      "enabled": true,
      "fallback_chain": []
    }
  },
  "providers": {
    "ce_remote": {
      "identifier": "ce_remote",
      "status": "enabled",
      "kind": "remote_model",
      "metadata_ref": "taiwan_tongues_ce",
      "endpoint_name": "ai-elder-care-asr-ce"
    }
  },
  "model_metadata": {
    "taiwan_tongues_ce": {
      "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
      "revision": "v2.0",
      "license": "other",
      "access_status": "open",
      "usage_restriction": "colab_validation_only",
      "approval_state": "not_approved",
      "production_gate": {
        "colab_validation_passed": false,
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

### Providers

| 欄位 | 規則 |
|---|---|
| `identifier` | 必填非空白 ID；Terraform 使它與 map key 相同 |
| `status` | `enabled`、`disabled` 或 `colab_only` |
| `kind` | `mock` 或 `remote_model`；不得新增 local/AWS managed 路徑 |
| `metadata_ref` | remote model 必填，指向 `model_metadata` |
| `endpoint_name` | remote model 必填 |

Lambda 不管理程序內模型槽、併發數或等待佇列；容量與擴縮由 SageMaker endpoint
負責。因此設定中沒有 `max_concurrent` 或 `concurrency`。

### Model metadata 與 production gate

`access_status` 為 `open|gated|restricted`；`usage_restriction` 為
`colab_validation_only|production`；`approval_state` 為
`not_approved|approved|pending`。

遠端模型只有以下條件全部成立才可建立 provider：

- `usage_restriction=production`
- `approval_state=approved`
- `colab_validation_passed`、`license_cleared`、`access_granted`、
  `quota_cleared`、`runtime_capacity_verified` 全為 true

缺少 gate 欄位視為 false。`approval_record_ref` 可為 ADR path 或 null。固定模型事實與目前
狀態見 [`model-catalog.md`](model-catalog.md)。

六腔值由 Chat profile 的 `HakkaDialect` 型別約束；Lambda 只以它選擇 route，且不得把
Formo prompt ID 放入 `ASR_CONFIG_JSON` 或 SageMaker request。prompt 固定在各 endpoint
部署設定。

## Fail-closed 驗證

以下任一情況拒絕整份設定：

- 缺少頂層 `routes`、`providers` 或 `model_metadata`。
- route 參照未知或重複 provider。
- remote provider 缺 `metadata_ref` 或 `endpoint_name`。
- 宣告 production 但 approval state 或五項 gate 不完整。
- enum、boolean 或 collection 型別不符。

## Terraform 行為

- `asr_enable_endpoints=false`：注入兩種語言皆停用的 production 設定，不啟用 mock。
- `asr_enable_endpoints=true`：填入 CE 與六腔 Formo endpoints，但模型仍須獨立通過核准。
- 建立 endpoint 不等於核准模型；未核准 provider 不會被 composition 建立，也不會外呼。
