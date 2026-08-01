# Title

Taiwan-Tongues-ASR-CE Production Approval

## Status

未核准（2026-08-01）

## Date

2026-08-01

## Owners

- ASR 維護者

## Scope

本 ADR 記錄 Taiwan-Tongues-ASR-CE 是否可由智慧長照陪伴 App 的 SageMaker
remote-only ASR 路徑執行 production invocation。CE 的預定角色是 `zh-TW` 與六腔
`hak` 的共同備援，不是中文主力。

## Candidate Models

| Model ID | Language | License | Access Status | Usage Restriction |
|---|---|---|---|---|
| `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0` | `zh-TW`、`hak` | `other` | `open` | `staging_validation_only` |

固定模型規格見 [`docs/asr/model-catalog.md`](../asr/model-catalog.md)。

## Evidence References

目前沒有可支持 production 核准的 staging/runtime evidence。未來只可引用去識別化 fixture
ID、聚合辨識品質／延遲／容量結果與固定 failure category；不得保存音訊、完整逐字稿、
token、prompt、endpoint、原始 provider response 或原始例外。

## Production Gate Status

| Gate Item | Approved | Evidence／阻礙 |
|---|---|---|
| `staging_validation_passed` | false | 尚未在指定 `ml.g5.4xlarge` 完成中／客語品質與延遲驗收 |
| `license_cleared` | false | `other` 授權尚未取得 production 用途核准 |
| `access_granted` | false | Repository 為 open，但尚未留下 artifact 存取驗證紀錄 |
| `quota_cleared` | false | 尚未在實際競賽帳號確認 endpoint 建立與可用配額 |
| `runtime_capacity_verified` | false | 尚無該 instance 上的 image、artifact、GPU 記憶體、吞吐與併發實測 |

## Decision

目前不核准 production invocation。Terraform 與 Lambda 設定必須維持
`staging_validation_only`、`not_approved`；即使 provider status 為 enabled，model gate 仍須
阻止它作為 production fallback 被呼叫。

## Rationale

CE 目前只有候選模型事實與部署骨架，尚未具備授權核准、指定 instance staging 結果、
實際帳號配額及 runtime 容量證據。備援角色不降低 production gate；預先開啟任一缺少證據的
gate 都會違反 ASR fail-closed 原則。

## Risks

- CE 輸出文字的語言不保證，可能影響中／客語下游品質。
- `other` 授權的允許用途尚不明確。
- 尚未測量真實推論延遲、GPU 記憶體與併發容量。

## Non Goals

- 本 ADR 不建立 SageMaker endpoint 或執行 production invocation。
- 本 ADR 不改變 Amazon Transcribe／Formo 的主力角色。
- 本 ADR 不修改公開 API。

## Follow Up Actions

- [ ] 在 `ml.g5.4xlarge` staging endpoint 完成中／客語辨識品質與延遲驗收。
- [ ] 取得授權用途核准。
- [ ] 確認實際競賽帳號配額。
- [ ] 建立並測試 inference container 與 artifact。
- [ ] 完成 GPU 記憶體、吞吐與併發容量驗證。
- [ ] 所有 gate 通過後重新審查本 ADR。
