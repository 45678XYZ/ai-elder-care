---
name: task-execution-efficiency
description: "Guidelines for executing spec tasks efficiently: clear convergence criteria, minimal verification, avoid redundant exploration. Activate when planning tasks, executing tasks, running specs, or working on implementation plans."
---

# 任務執行加速指引

減少 spec task 執行耗時：明確收斂條件、精簡驗證、避免多餘探索。

---

## 1. 任務編排原則（寫 tasks 時）

### 收斂條件必須具體可驗證

每個 task 必須有明確的「完成判定」，只用可自動驗證的條件：

| 好的收斂條件 | 壞的收斂條件 |
|---|---|
| `python -m pytest tests/xxx -q` 全數通過 | 「確認功能正常」 |
| `grep -r "SymbolName" backend/` 零命中 | 「確認已移除」 |
| 指定檔案存在且含特定段落 | 「文件已更新」 |
| `git diff --stat` 只動指定檔案 | 「不影響其他功能」 |

### 避免開放式的 Demo 描述

- 不寫「Demo：可展示 X 功能」— agent 不確定何時收斂
- 改為具體的自動化驗證步驟，或省略 Demo（程式碼 + 測試本身就是 demo）

### 一個 task 只做一件事

- 不混合「修改程式」與「更新文件」與「重構測試」在同一 task
- 拆成獨立 task 讓每個更快完成、更容易判斷是否完成

### 預先宣告影響範圍

Task 描述中列出會修改的檔案路徑，讓 agent 不必花時間探索：

```
影響檔案：
- backend/src/shared/asr/config.py（新增欄位）
- backend/tests/asr/test_composition.py（新增測試）
- docs/asr/config-schema.md（同步 schema）
```

---

## 2. 任務執行原則（跑 tasks 時）

### 讀檔策略：精準讀取，不全面探索

- 已在 task 描述中列出路徑 → 直接讀取，不用 context-gatherer
- 只有真的不確定哪些檔案相關時才用子代理探索
- 同一 session 內不重複讀取相同檔案

### 測試策略：跑最小必要範圍

| 修改範圍 | 跑什麼測試 | 不跑什麼 |
|---|---|---|
| 單一模組 `asr/config.py` | `pytest tests/asr/test_composition.py tests/asr/test_task_2_1_validation.py -q` | 整個 `tests/asr` |
| 多個 ASR 模組 | `pytest tests/asr -q` | 加上 `tests/` 其他目錄 |
| 跨模組（含 chat bridge） | `pytest tests/asr -q` | 不需跑兩次 |
| 只改文件 | 不跑測試 | — |
| 只改 Terraform | 不跑 pytest | — |

### 避免重複驗證

- 測試通過一次就夠，不要為了「確認」再跑一次
- grep 確認零命中一次就夠，不要換關鍵字再搜一次
- 檔案存在性確認一次就夠

### 跳過不必要的步驟

- 不需要在每個 task 結束時都列出完整摘要 — 完成就往下走
- 不需要在修改前後都讀同一個檔案（修改前讀一次即可）
- diagnostics / lint 只在新增程式碼時用，純刪除或文件修改不需要

---

## 3. Git 操作時機

- **不要每個 task 完成就 commit** — 等使用者指示
- 需要 commit 時按 `docs/workflow.md` 拆 commit（一個 commit 一個 concern）
- 跑測試是 commit 前的最後一步，不是每個中間步驟都跑

---

## 4. 文件修改的收斂

修改文件時，收斂條件是：

1. 內容正確（符合實際程式碼行為）
2. 跨文件連結指向存在的檔案
3. 不重複其他文件已有的內容

**不需要**：反覆走讀確認「完整性」、逐字對照程式碼、搜尋所有可能遺漏。

---

## 5. 錯誤處理

- 第一次失敗：檢查錯誤訊息，修正，重試
- 第二次同樣失敗：停下來換做法，不做第三次同樣嘗試
- 測試失敗：只修相關的失敗，不要「順便」改其他東西

---

## 6. 子代理使用時機

| 情境 | 用不用子代理 |
|---|---|
| task 已明確列出影響檔案 | 不用，直接讀 |
| 首次接觸不熟悉的模組 | 用 context-gatherer 一次 |
| 需要跨 10+ 檔案搜尋引用 | 用 grep_search，不用子代理 |
| 需要平行處理獨立子任務 | 用 general-task-execution |

---

## 7. 快速參考：常用驗證命令

```powershell
# Backend 測試（最常用）
cd backend; python -m pytest tests/asr -q

# 單一測試檔
cd backend; python -m pytest tests/asr/test_composition.py -q

# 搜尋殘留引用
grep -r "SymbolName" backend/src/ --include="*.py"

# 檔案存在性批次確認
@("path1", "path2") | ForEach-Object { Test-Path $_ }

# Terraform 驗證
cd terraform; terraform fmt -check; terraform validate

# Import smoke test
cd backend; python -c "from src.shared.asr import get_asr_facade; print('OK')"
```
