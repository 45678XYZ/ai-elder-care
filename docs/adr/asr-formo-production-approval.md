# Title

FormoSpeech Whisper-v3 Production Approval

## Status

未核准（2026-07-31）

## Date

2026-07-31

## Owners

- ASR 維護者

## Scope

本 ADR 記錄 FormoSpeech Whisper-v3 是否可由智慧長照陪伴 App 的 SageMaker
remote-only ASR 路徑執行 production invocation。

## Candidate Models

| Model ID | Language | License | Access Status | Usage Restriction |
|---|---|---|---|---|
| `formospeech/whisper-large-v3-taiwanese-hakka` | `hak` | CC BY-NC 4.0 | `gated` | `colab_validation_only` |

固定模型規格見 [`docs/asr/model-catalog.md`](../asr/model-catalog.md)。

## Evidence References

目前沒有可支持 production 核准的 evidence record。未來只可填入 `run_id`、
`model_id`、`input_fixture_id`、`outcome`、`failure_category` 五個欄位。

## AWS Capability Gate Status

| Gate Item | Approved | Evidence／阻礙 |
|---|---|---|
| `colab_validation_passed` | false | 尚未完成 Formo Colab 人工驗證 |
| `license_cleared` | false | CC BY-NC 4.0 只允許非商業用途，尚未核准專案用途 |
| `access_granted` | false | gated-model 存取申請尚未完成 |
| `quota_cleared` | false | 尚未確認 SageMaker 額度 |
| `runtime_capacity_verified` | false | 尚無 container image、artifact、prompt 選擇與 GPU 容量實測 |

## Decision

目前不核准 production invocation。Terraform 與 Lambda 設定必須維持
`colab_validation_only`、`not_approved`，五項 production gate 全為 `false`。

## Rationale

目前尚未取得 gated-model 存取權、未完成 Colab 驗證，也沒有授權、額度與
runtime 容量證據。Formo prompt 亦尚未完成部署決策，因此不得建立可呼叫的
remote provider。

## Risks

- CC BY-NC 4.0 不允許商業用途。
- gated-model 存取可能延遲或被拒絕。
- 不同客語腔調需要明確的部署 prompt 決策。
- 尚未測量真實推論延遲、GPU 記憶體與併發容量。

## Non Goals

- 本 ADR 不建立 SageMaker endpoint。
- 本 ADR 不執行 production invocation。
- 本 ADR 不把 prompt ID 傳入 Lambda request。
- 本 ADR 不修改公開 API 或選擇其他模型。

## Follow Up Actions

- [ ] 取得 Hugging Face gated-model 存取權。
- [ ] 完成 Formo Colab 人工驗證並產生去識別化 evidence。
- [ ] 確認非商業用途與授權義務。
- [ ] 選定部署腔調 prompt。
- [ ] 確認 SageMaker 額度。
- [ ] 建立並測試 inference container、artifact 與 runtime 容量。
- [ ] 所有 gate 通過後重新審查本 ADR。
