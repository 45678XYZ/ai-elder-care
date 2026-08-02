# Title

FormoSpeech Whisper-v3 Production Approval

## Status

未核准（2026-08-01；gated access 已取得）

## Date

2026-08-01

## Owners

- ASR 維護者

## Scope

本 ADR 記錄 FormoSpeech Whisper-v3 是否可由智慧長照陪伴 App 的六個 SageMaker
remote-only ASR endpoints 執行 production invocation。每腔 endpoint 固定一個 prompt，
作為該腔客語主力；CE 是共同備援。

## Candidate Models

| Model ID | Language | License | Access Status | Usage Restriction |
|---|---|---|---|---|
| `formospeech/whisper-large-v3-taiwanese-hakka` | `hak` | CC BY-NC 4.0 | `gated`（已取得存取權） | `staging_validation_only` |

固定模型規格見 [`docs/asr/model-catalog.md`](../asr/model-catalog.md)。

## Evidence References

專案 owner 已確認 gated repository 存取權取得；repo 不保存 HF token 或 credential。除此之外，
目前沒有可支持 production 核准的 staging/runtime evidence。後續紀錄只能使用去識別化 fixture
ID、聚合辨識品質／延遲／容量結果與固定 failure category。

## Production Gate Status

| Gate Item | Approved | Evidence／阻礙 |
|---|---|---|
| `staging_validation_passed` | false | 尚未在三種指定 instance 上完成六腔品質與延遲驗收 |
| `license_cleared` | false | CC BY-NC 4.0 用途與歸屬義務尚未正式核准 |
| `access_granted` | true | 專案 owner 於 2026-08-01 確認 gated access 已取得；不保存 token |
| `quota_cleared` | false | 尚未在實際競賽帳號驗證六個 endpoint 可建立 |
| `runtime_capacity_verified` | false | 尚無 image、artifact、GPU 記憶體、吞吐與併發實測 |

## Decision

目前不核准 production invocation。Terraform 與 Lambda 設定必須維持
`staging_validation_only`、`not_approved`；即使 provider status 為 enabled，model gate 仍須阻止
production invocation。
取得 gated access 只通過單一 gate，不能推導其他 gate 或 production 核准。

## Deployment Decision

- 六腔 prompt 固定為各自 wire value：`htia_sixian`、`htia_hailu`、`htia_dapu`、
  `htia_raoping`、`htia_zhaoan`、`htia_nansixian`。
- 每個 endpoint 固定 `FORMO_GENERATION_LANGUAGE=Chinese`，使 Whisper 輸出客語漢字；
  capability 仍只允許 `hak`。
- 四縣／海陸用 `ml.g5.2xlarge`，大埔／饒平用 `ml.g5.xlarge`，詔安／南四縣用
  `ml.g4dn.2xlarge`；每端點固定一台且不建立 autoscaling。
- Lambda request 不傳 prompt ID 或 generation language。

## Rationale

模型存取障礙已解除，但非商業授權核准、指定 instance staging 結果、實際帳號配額與
runtime 容量證據仍缺。部署 prompt 與漢字解碼設定已決定，不等於其品質或容量通過驗收。

## Risks

- CC BY-NC 4.0 不允許商業用途。
- `Chinese` 是 generation 設定，不代表可取代中文 Transcribe。
- 六腔與三種 instance 尚未完成真實延遲、記憶體與容量測量。

## Non Goals

- 本 ADR 不建立 SageMaker endpoint 或執行 production invocation。
- 本 ADR 不把 prompt ID、generation language 或 HF token 傳入 Lambda request。
- 本 ADR 不修改公開 API。

## Follow Up Actions

- [x] 取得 Hugging Face gated-model 存取權。
- [ ] 在指定 instance 的 staging endpoints 完成六腔品質與延遲驗收。
- [ ] 確認非商業用途與授權義務。
- [ ] 確認實際競賽帳號配額。
- [ ] 建立並測試 inference container、artifact 與 runtime 容量。
- [ ] 所有 gate 通過後重新審查本 ADR。
