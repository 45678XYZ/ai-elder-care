# Title

ASR Model Validation ADR Template

## Status

Draft

## Date

YYYY-MM-DD

## Owners

- (填入負責人)

## Scope

本 ADR 記錄單一 ASR 模型的驗證結果與 production 核准決策。每個模型必須使用
獨立 ADR，不得以一份共同紀錄代替不同授權、存取與容量條件。

## Candidate Models

| Model ID | Language | License | Access Status | Usage Restriction |
|----------|----------|---------|---------------|-------------------|
| (model_id) | (language) | (license) | (access_status) | colab_validation_only |

## Evidence References

僅允許以下五個欄位，不得寫入完整 transcript、token、audio、prompt、raw provider response 或其他敏感內容。

| run_id | model_id | input_fixture_id | outcome | failure_category |
|--------|----------|------------------|---------|------------------|
| (run_id) | (model_id) | (fixture_id) | success/failure | (category or N/A) |

## AWS Capability Gate Status

五項 gate 全部核准後，模型才可標記為 production。核准前 Terraform 與
`ASR_CONFIG_JSON` 必須維持 fail closed。

| Gate Item | Approved | Evidence／阻礙 |
|---|---|---|
| `colab_validation_passed` | false | - |
| `license_cleared` | false | - |
| `access_granted` | false | - |
| `quota_cleared` | false | - |
| `runtime_capacity_verified` | false | - |

## Decision

(待決定)

## Rationale

(決策理由)

## Risks

- (列出已知風險)

## Non Goals

- 核准前不得對真實使用者音訊執行 production invocation。
- 本 ADR 不建立 endpoint、不修改公開 API，也不改變 remote-only 架構。
- Evidence 不得包含完整逐字稿、token、音訊、prompt 或 provider 原始回應。

## Follow Up Actions

- [ ] 完成 Colab 人工驗證
- [ ] 確認授權與模型存取權
- [ ] 確認 SageMaker 額度與 runtime 容量
- [ ] 五項 gate 通過後更新 ADR 決策
