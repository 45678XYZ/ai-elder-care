# Title

Taiwan-Tongues-ASR-CE Production Approval

## Status

未核准（2026-07-31）

## Date

2026-07-31

## Owners

- ASR 維護者

## Scope

本 ADR 記錄 Taiwan-Tongues-ASR-CE 是否可由智慧長照陪伴 App 的 SageMaker
remote-only ASR 路徑執行 production invocation。

## Candidate Models

| Model ID | Language | License | Access Status | Usage Restriction |
|---|---|---|---|---|
| `adi-gov-tw/Taiwan-Tongues-ASR-CE-v2.0` | `zh-TW`、`hak` | `other` | `open` | `colab_validation_only` |

固定模型規格見 [`docs/asr/model-catalog.md`](../asr/model-catalog.md)。

## Evidence References

目前沒有可支持 production 核准的 evidence record。未來只可填入 `run_id`、
`model_id`、`input_fixture_id`、`outcome`、`failure_category` 五個欄位。

## AWS Capability Gate Status

| Gate Item | Approved | Evidence／阻礙 |
|---|---|---|
| `colab_validation_passed` | false | 尚未完成 CE Colab 人工驗證 |
| `license_cleared` | false | `other` 授權尚未取得 production 用途核准 |
| `access_granted` | false | 尚未留下可追溯的存取驗證紀錄 |
| `quota_cleared` | false | 尚未確認 SageMaker 額度 |
| `runtime_capacity_verified` | false | 尚無 container image、artifact 與 GPU 容量實測 |

## Decision

目前不核准 production invocation。Terraform 與 Lambda 設定必須維持
`colab_validation_only`、`not_approved`，五項 production gate 全為 `false`。

## Rationale

目前只有模型候選資料與 Colab 驗證套件，尚未具備人工驗證結果、授權核准、
SageMaker 額度及實際 runtime 容量證據。任何預先開啟 gate 的設定都會違反
ASR fail-closed 原則。

## Risks

- CE 輸出文字的語言不保證，可能影響下游對話品質。
- `other` 授權的允許用途尚不明確。
- 尚未測量真實推論延遲、GPU 記憶體與併發容量。

## Non Goals

- 本 ADR 不建立 SageMaker endpoint。
- 本 ADR 不執行 production invocation。
- 本 ADR 不修改公開 API 或選擇其他模型。

## Follow Up Actions

- [ ] 完成 CE Colab 人工驗證並產生去識別化 evidence。
- [ ] 取得授權用途核准。
- [ ] 確認 SageMaker 額度。
- [ ] 建立並測試 inference container 與 artifact。
- [ ] 完成延遲、GPU 記憶體與併發容量驗證。
- [ ] 所有 gate 通過後重新審查本 ADR。
