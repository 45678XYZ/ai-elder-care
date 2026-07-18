# 開發流程

## 分支

- `main` 為主分支，隨時保持可部署狀態，不直接 push
- 開發一律從最新的 `main` 開分支，新功能用 `feature/`、修 bug 用 `fix/`：

```bash
git checkout main
git pull
git checkout -b feature/<簡短描述>   # 或 fix/<簡短描述>
```

- 分支名稱用小寫英文與連字號，如 `feature/chat-api`、`fix/reminder-timezone`

## Commit

採用 [Conventional Commits](https://www.conventionalcommits.org/)，格式：

```
<type>(<scope>): <subject>
```

- `type` 依修改性質選用：

| type | 使用時機 |
|---|---|
| `feat` | 新增功能 |
| `fix` | 修正 bug |
| `docs` | 只改文件 |
| `style` | 格式調整，不影響程式邏輯，如排版、分號 |
| `refactor` | 重構，不改變外部行為 |
| `test` | 新增或修改測試 |
| `chore` | 雜項，如建置設定、依賴更新 |

- `scope` 選填，標明影響範圍，如 `feat(chat): ...`、`chore(terraform): ...`
- `subject` 用英文、祈使句、不加句號，如 `feat(chat): add audio input support`
- 一個 commit 只做一件事；不同性質的修改（功能、文件、雜項）拆成不同 commit

## Pull Request

- 分支完成後推上遠端，開 PR 到 `main`
- PR 標題英文首字大寫，簡述改了什麼
- 不強制 code review，確認以下事項後即可自行 merge：
  - 自己重看過一次 diff，沒有夾帶無關修改
  - 沒有把金鑰、憑證或個資 commit 進來
  - 行為或 API 有變動時，`docs/` 內相關文件已同步更新
  - 分支已更新到最新的 `main`，沒有衝突