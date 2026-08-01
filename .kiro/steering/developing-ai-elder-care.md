---
inclusion: always
---

# Repo Guide for Agent Tools

Orientation for any AI coding tool (and teammates) working in this repo. It is deliberately thin: for each topic it points to the authoritative doc instead of repeating it, so nothing drifts out of sync. **Read the referenced file before acting on that topic.**

Paths below are relative to the repository root, so they resolve from your workspace no matter where this skill is installed.

## Which doc owns what

先依工作範圍選擇權威文件；不要為局部修改預載整份系統文件。只有變更系統架構、資料模型、模組邊界或 repo 佈局時才完整閱讀 `docs/framework.md`。

| Topic | Authoritative doc |
|---|---|
| Architecture, DynamoDB data model & write rules, module breakdown, repo layout | `docs/framework.md` |
| API endpoints, request/response, error format | `docs/api.md` — the single source of truth between frontend and backend |
| Naming, comments, and code style | `docs/conventions.md` |
| PII & security (auth, encryption, consent, mock data) | `docs/pii.md` |
| User journeys (elder & caregiver) | `docs/user-journey.md` |
| Branching, commit format, PR & merge rules | `docs/workflow.md` |
| Backend setup & running tests | `backend/README.md` |
| ASR/TTS 與 Chat 語音串接 | `developing-ai-elder-care-speech` skill；依其中的按需閱讀表載入文件 |

## Rules for agents

Not spelled out in the docs above, or worth emphasizing:

- **Git safety**: never commit or push without the user's explicit instruction; merge/rebase (history rewrites) need confirmation too. When you do commit, follow `docs/workflow.md` — one commit per concern, staged selectively, never a blanket `git add -A`.
- **Follow the conventions** in `docs/conventions.md` for naming, comments, and code style. In short: docs, comments, and docstrings in Traditional Chinese; code identifiers, commit messages, and branch names in English.
- **API contract**: `docs/api.md` is the frontend/backend contract — whenever you change API behavior, update it in the same change.
- **Keep docs in sync**: when you change a doc's content, or add/move a doc or top-level file/dir, update whatever references it — the README structure tree and its doc list, and cross-links in related docs — so nothing goes stale.
- **Before committing**: run the checks for the area you touched (e.g. `python -m pytest` in `backend/`), per `docs/conventions.md`.
- **Speech remote-only**: Lambda 不載入 ASR／TTS 模型；受控 Amazon Transcribe Streaming
  `zh-TW` 與 Polly 是允許的 AWS managed providers。CE/Formo 與自託管 TTS production
  核准只接受指定 SageMaker instance 的 staging/runtime evidence；未核准一律 fail closed。
- **Talk with the user in Traditional Chinese.**
