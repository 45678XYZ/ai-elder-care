---
name: developing-ai-elder-care
description: Guide to the ai-elder-care project (Flutter app + AWS Python Lambda + Terraform): which doc is authoritative for each topic, plus the workflow and coding conventions to follow. Use at the start of any development task in this repo — writing code, branching, committing, opening PRs, or editing docs.
---

# Repo Guide for Agent Tools

Orientation for any AI coding tool (and teammates) working in this repo. It is deliberately thin: for each topic it points to the authoritative doc instead of repeating it, so nothing drifts out of sync. **Read the referenced file before acting on that topic.**

Paths below are relative to the repository root, so they resolve from your workspace no matter where this skill is installed.

## Which doc owns what

New here? Read `docs/framework.md` first — it's the big picture (modules, architecture, data model, repo layout). Then use this table to find the authoritative doc for a topic and read it before acting:

| Topic | Authoritative doc |
|---|---|
| Architecture, DynamoDB data model & write rules, module breakdown, repo layout | `docs/framework.md` |
| API endpoints, request/response, error format | `docs/api.md` — the single source of truth between frontend and backend |
| Naming, comments, and code style | `docs/conventions.md` |
| PII & security (auth, encryption, consent, mock data) | `docs/pii.md` |
| User journeys (elder & caregiver) | `docs/user-journey.md` |
| Branching, commit format, PR & merge rules | `docs/workflow.md` |
| Backend setup & running tests | `backend/README.md` |

## Rules for agents

Not spelled out in the docs above, or worth emphasizing:

- **Git safety**: never commit or push without the user's explicit instruction; merge/rebase (history rewrites) need confirmation too. When you do commit, follow `docs/workflow.md` — one commit per concern, staged selectively, never a blanket `git add -A`.
- **Follow the conventions** in `docs/conventions.md` for naming, comments, and code style. In short: docs, comments, and docstrings in Traditional Chinese; code identifiers, commit messages, and branch names in English.
- **API contract**: `docs/api.md` is the frontend/backend contract — whenever you change API behavior, update it in the same change.
- **Before committing**: run the checks for the area you touched (e.g. `python -m pytest` in `backend/`), per `docs/conventions.md`.
- **Talk with the user in Traditional Chinese.**
