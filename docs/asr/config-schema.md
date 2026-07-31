# ASR 設定規格 — `ASR_CONFIG_JSON`

本文件定義 `ASR_CONFIG_JSON` 的完整 JSON schema、每個欄位的語意與合法值域、
設定錯誤時的 fail-closed 處理，以及 Terraform 如何組裝此 JSON。

相關文件：
- 架構入口：[`docs/asr/framework.md`](./framework.md)
- Lambda 端 parser：`backend/src/shared/asr/config.py` → `parse_asr_config()`
- Terraform 組裝：`terraform/asr_lambda_config.tf`

---

## 設定來源

`ASR_CONFIG_JSON` 是 Lambda 唯一的 ASR 設定來源。

- 環境變數未設定或為空白：使用 `default_config()`（僅 hak_mock 可用）。
- 環境變數有值但 JSON 解析失敗：拋 `ConfigParseError`，**不退回預設值**。
- 環境變數有值且解析成功：依 schema 驗證後建立 `AsrConfig`。

---

## JSON Schema

```json
{
  "routes": {
    "<language_code>": {
      "route": "<route_name>",
      "provider_identifier": "<provider_id>",
      "enabled": true,
      "fallback_chain": ["<provider_id>", ...]
    }
  },
  "providers": {
    "<provider_id>": {
      "identifier": "<provider_id>",
      "status": "enabled" | "disabled" | "colab_only",
      "kind": "mock" | "remote_model",
      "metadata_ref": "<model_metadata_key>" | null,
      "max_concurrent": 4,
      "endpoint_name": "<sagemaker_endpoint_name>" | null
    }
  },
  "model_metadata": {
    "<metadata_key>": {
      "model_id": "<huggingface_model_id>",
      "revision": "<version>",
      "license": "<license_identifier>",
      "access_status": "open" | "gated" | "restricted",
      "usage_restriction": "colab_validation_only" | "production",
      "approval_state": "not_approved" | "approved" | "pending",
      "production_gate": {
        "colab_validation_passed": false,
        "license_cleared": false,
        "access_granted": false,
        "quota_cleared": false,
        "runtime_capacity_verified": false,
        "approval_record_ref": "<adr_path>" | null
      }
    }
  },
  "formo_prompt_id_allowlist": [
    "htia_sixian", "htia_hailu", "htia_dapu",
    "htia_raoping", "htia_zhaoan", "htia_nansixian"
  ],
  "concurrency": {
    "spill_wait_ms": 250,
    "model_load_wait_ms": 15000,
    "load_retry_cooldown_seconds": 60.0
  }
}
```

---

## 欄位語意

### `routes`（必填）

每個 key 是語言代碼（`zh-TW`、`hak`），值為路由設定：

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `route` | string | 是 | 路由名稱（供遙測識別） |
| `provider_identifier` | string | 是 | 主 provider ID |
| `enabled` | boolean | 是 | 路由是否啟用 |
| `fallback_chain` | string[] | 否 | 備援 provider ID 序列（預設 `[]`） |

約束：
- `provider_identifier` 不得出現在 `fallback_chain` 中
- `fallback_chain` 不得有重複
- 所有 provider ID 必須存在於 `providers` 區段

### `providers`（必填）

每個 key 是 provider ID，值為 provider 設定：

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `identifier` | string | 是 | 必須與 key 相同 |
| `status` | enum | 是 | `enabled`／`disabled`／`colab_only` |
| `kind` | enum | 否 | `mock`（預設）或 `remote_model` |
| `metadata_ref` | string? | 否 | 指向 `model_metadata` 的 key |
| `max_concurrent` | integer | 否 | ≥ 1，預設 1 |
| `endpoint_name` | string? | 否 | SageMaker endpoint 名稱，`remote_model` 必填 |

### `model_metadata`（必填）

每個 key 是 metadata 識別鍵，值為模型中繼資料：

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `model_id` | string | 是 | HuggingFace model ID |
| `revision` | string | 是 | 模型版本 |
| `license` | string | 是 | 授權識別 |
| `access_status` | enum | 是 | `open`／`gated`／`restricted` |
| `usage_restriction` | enum | 是 | `colab_validation_only`／`production` |
| `approval_state` | enum | 是 | `not_approved`／`approved`／`pending` |
| `production_gate` | object? | 否 | 缺失視為全 false（未核准） |

### `production_gate`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `colab_validation_passed` | boolean | 已在 Colab 人工驗證跑出可用結果 |
| `license_cleared` | boolean | 授權允許實際用途 |
| `access_granted` | boolean | gated model 存取權已取得 |
| `quota_cleared` | boolean | 推論額度足夠 |
| `runtime_capacity_verified` | boolean | 執行環境已實測可承載 |
| `approval_record_ref` | string? | 核准 ADR 路徑 |

五項全為 `true` 才算核准。非 `true` 的值（包括缺失）一律視為 `false`。

### `formo_prompt_id_allowlist`（必填）

精確等於六個允許值的集合。Parser 在值不完全匹配時 fail closed。

### `concurrency`（選填）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `spill_wait_ms` | integer | 250 | 主 provider 飽和時的排隊上限 |
| `model_load_wait_ms` | integer | 15000 | 等待 handle 建立的上限 |
| `load_retry_cooldown_seconds` | number | 60.0 | 載入失敗後的冷卻期 |

---

## 矛盾狀態驗證

Parser 在以下情況 fail closed：

1. `usage_restriction=production` 但 `approval_state` 不是 `approved`
2. `usage_restriction=production` 且 `approval_state=approved` 但 `production_gate` 不完整
3. Route 引用不存在的 provider
4. `kind=remote_model` 但缺少 `endpoint_name`

---

## Terraform 如何組裝

`terraform/asr_lambda_config.tf` 從 Terraform 變數與資源名稱組裝 JSON：

```hcl
locals {
  asr_config_json = jsonencode({
    routes         = { ... }  # 從 endpoint 啟用狀態決定
    providers      = { ... }  # 從 endpoint name 填入
    model_metadata = { ... }  # 從 var.asr_* 變數填入
    formo_prompt_id_allowlist = [...]
    concurrency    = { ... }
  })
}
```

- `asr_enable_endpoints = false` 時：仍注入明確的 production-disabled 設定；
  `zh-TW`／`hak` route 都停用，不建立 provider、不外呼，也不啟用 `hak_mock`。
- `asr_enable_endpoints = true` 時：Terraform 會填入 endpoint 與遠端 provider，
  但模型仍須另外通過個別 ADR 與五項 production gate。
- 建立 endpoint 不代表模型獲准上線；目前 CE、Formo 都是
  `colab_validation_only`／`not_approved`，因此不建立 remote provider。
- 模型固定規格與目前核准狀態見
  [`docs/asr/model-catalog.md`](./model-catalog.md)。

---

## 合法設定範例

以下「僅 mock」範例只供單元測試或明確的本機開發使用，不得作為 production
`ASR_CONFIG_JSON`。production 在 endpoint 關閉時使用 Terraform 產生的 disabled routes。

### 最小可用設定（僅 mock）

```json
{
  "routes": {
    "hak": {
      "route": "hak_primary",
      "provider_identifier": "hak_mock",
      "enabled": true
    }
  },
  "providers": {
    "hak_mock": {
      "identifier": "hak_mock",
      "status": "enabled",
      "kind": "mock"
    }
  },
  "model_metadata": {},
  "formo_prompt_id_allowlist": [
    "htia_sixian", "htia_hailu", "htia_dapu",
    "htia_raoping", "htia_zhaoan", "htia_nansixian"
  ]
}
```

### Production 設定（CE 已核准）

```json
{
  "routes": {
    "hak": {
      "route": "hak_primary",
      "provider_identifier": "ce_remote",
      "enabled": true,
      "fallback_chain": []
    },
    "zh-TW": {
      "route": "zh_tw_primary",
      "provider_identifier": "ce_remote",
      "enabled": true
    }
  },
  "providers": {
    "ce_remote": {
      "identifier": "ce_remote",
      "status": "enabled",
      "kind": "remote_model",
      "metadata_ref": "taiwan_tongues_ce",
      "max_concurrent": 4,
      "endpoint_name": "ai-elder-care-asr-ce"
    }
  },
  "model_metadata": {
    "taiwan_tongues_ce": {
      "model_id": "adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0",
      "revision": "v2.0",
      "license": "other",
      "access_status": "open",
      "usage_restriction": "production",
      "approval_state": "approved",
      "production_gate": {
        "colab_validation_passed": true,
        "license_cleared": true,
        "access_granted": true,
        "quota_cleared": true,
        "runtime_capacity_verified": true,
        "approval_record_ref": "docs/adr/asr-ce-production-approval.md"
      }
    }
  },
  "formo_prompt_id_allowlist": [
    "htia_sixian", "htia_hailu", "htia_dapu",
    "htia_raoping", "htia_zhaoan", "htia_nansixian"
  ],
  "concurrency": {
    "spill_wait_ms": 250,
    "model_load_wait_ms": 15000,
    "load_retry_cooldown_seconds": 60.0
  }
}
```

---

## 非法設定範例（fail closed）

| 情境 | 錯誤原因 |
|---|---|
| `usage_restriction=production` + `approval_state=not_approved` | 矛盾：宣告 production 但未核准 |
| `kind=remote_model` 但 `endpoint_name` 為 null | 遠端 provider 必須有 endpoint |
| `fallback_chain` 含主 provider | 備援不得重複主 provider |
| `formo_prompt_id_allowlist` 少一個值 | 必須精確六個 |
| Route 引用 `"unknown_provider"` | Provider 不存在 |
