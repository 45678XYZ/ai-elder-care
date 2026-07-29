# Title

ASR Model Validation ADR Template

## Status

Draft

## Date

YYYY-MM-DD

## Owners

- (填入負責人)

## Scope

本 ADR 記錄 ASR 模型候選評估與驗證決策，範圍限於 Colab 驗證階段的模型能力確認，不涵蓋 production deployment 或 AWS 服務選定。

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

本節為外部核准紀錄，不推定任何 AWS 服務、Region 或 deployment decision。

| Gate Item | Approved | Approver | Date |
|-----------|----------|----------|------|
| Region zh-TW support | pending | - | - |
| Service input/output mode | pending | - | - |
| Canonical PCM compatibility | pending | - | - |
| Timeout and Cancellation | pending | - | - |
| IAM | pending | - | - |
| S3 necessity | pending | - | - |
| S3 result handling and cleanup | pending | - | - |

## Decision

(待決定)

## Rationale

(決策理由)

## Risks

- (列出已知風險)

## Non Goals

- Taiwan-Tongues-ASR-CE production invocation 在本期為禁止事項，不得對真實使用者音訊執行模型推論。
- FormoSpeech Whisper-v3 production invocation 在本期為禁止事項，不得對真實使用者音訊執行模型推論。
- AWS capability gate 僅為外部核准紀錄，不推定任何服務、Region 或 deployment decision。
- 本 ADR 不涵蓋 production endpoint 建立、AWS adapter 實作選定或正式部署資源配置。

## Follow Up Actions

- [ ] 完成 AWS Capability Gate 各項目核准
- [ ] 確認模型授權與存取限制是否允許後續階段使用
- [ ] 根據 Colab 驗證結果決定下一期模型選定方向
