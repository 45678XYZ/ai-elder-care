# AI Elder-Care Project Instructions

## Product

智慧長照陪伴 App：長者模式（免手持語音互動陪伴）＋照護者模式（生活記錄摘要與資訊介面）。

- 系統框架：`docs/framework.md`
- API 規格：`docs/api.md`（前後端的唯一契約，改動須同步）
- 架構原則：Flutter 端做薄、智慧邏輯放 AWS 後端（Python Lambda）；IaC 用 Terraform

## Repo Guide

本專案的權威文件依主題分工；針對主題採取行動前，先閱讀對應文件：

| 主題 | 權威文件 |
|---|---|
| 架構、DynamoDB 資料模型與寫入規則、模組分工、repo 佈局 | `docs/framework.md` |
| API endpoint、request/response、錯誤格式 | `docs/api.md`（前後端唯一契約） |
| 命名、註解與程式碼風格 | `docs/conventions.md` |
| PII 與安全（驗證、加密、同意、mock data） | `docs/pii.md` |
| 長者與照護者 user journey | `docs/user-journey.md` |
| 分支、commit、PR 與 merge 規則 | `docs/workflow.md` |
| Backend 設定與測試 | `backend/README.md` |

## Rules for Agents

- Git safety：未經使用者明確指示，不得 commit 或 push；merge/rebase（歷史重寫）也需要確認。執行 commit 時遵守 `docs/workflow.md`：每個 concern 一個 commit、選擇性 stage，不使用 blanket `git add -A`。
- 遵守 `docs/conventions.md` 的命名、註解與程式碼風格。簡要規則：文件、註解與 docstring 使用繁體中文；程式碼識別字、commit message 與 branch name 使用英文。
- API contract：`docs/api.md` 是前後端契約；任何 API 行為變更都必須在同一變更中更新該文件。
- 文件同步：修改文件或新增/移動文件與頂層目錄時，更新 README 結構樹、文件清單與相關文件的交叉連結。
- 只在修改系統架構、資料模型、模組邊界或 repo 佈局時閱讀完整的 `docs/framework.md`；局部修改依主題只讀權威文件與對應 skill 的按需閱讀表。完成後依受影響區域執行 `docs/conventions.md` 要求的檢查，例如 `backend/` 使用 `python -m pytest`。
- IaC 本機驗證可使用 OpenTofu CLI（`tofu init/fmt/validate/plan`），但不得把專案交付物轉成 OpenTofu：檔案維持 `.tf`、文件與架構名稱維持 Terraform，且指令造成的 `.terraform.lock.hcl` registry／hash 改寫須在工作完成前恢復為 Terraform 版本。未經使用者明確指示不得執行 `tofu apply/destroy`，規則等同 Terraform。
- 語音 remote-only 的意思是 Lambda 不載入 ASR／TTS 模型；受控的 Amazon Transcribe
  Streaming `zh-TW` 與 Polly 屬允許的 AWS managed providers。CE/Formo 與自託管 TTS
  production 核准只接受指定 SageMaker instance 的 staging/runtime evidence；未核准一律
  fail closed。
- 與使用者溝通時使用繁體中文。

## Skills

- Always follow the project rules in this file.
- Use `developing-ai-elder-care` for general elder-care project development.
- 修改 ASR、TTS、Chat 語音串接或其 Terraform／文件時，使用單一的 `developing-ai-elder-care-speech` skill。
- 專案規則與語音 skill 同時適用時，以專案規則為優先。

The original Kiro steering files remain under `.kiro/steering/`. If this file and the Kiro copies diverge, resolve the difference manually and keep both versions consistent.
