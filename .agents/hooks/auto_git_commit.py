"""Antigravity IDE Hook - Auto Git Commit after Implementation Plan Completion

Reads workspace git status, stages modified files, analyzes changes according to 
the Conventional Commits specification (.agents/skills/git-commit/SKILL.md),
and executes git commit automatically.
"""

import json
import os
import subprocess
import sys


def run_cmd(args: list[str]) -> str:
    """執行 Shell 指令並回傳 stdout，出錯時回傳空字串。"""
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Command failed: {' '.join(args)}\nStderr: {e.stderr}\n")
        return ""


def analyze_commit_info(changed_files: list[str]) -> tuple[str, str, str]:
    """依據變更檔案分析 Conventional Commit 之 (Type, Scope, Description)。"""
    types = set()
    scopes = set()

    for file_path in changed_files:
        path_lower = file_path.lower()
        if "docs/" in path_lower or path_lower.endswith(".md"):
            types.add("docs")
            scopes.add("docs")
        elif "tests/" in path_lower or "test_" in path_lower:
            types.add("test")
            scopes.add("tests")
        elif "terraform/" in path_lower:
            types.add("build")
            scopes.add("infra")
        elif "backend/src/shared/" in path_lower:
            types.add("feat")
            scopes.add("backend-shared")
        elif "backend/src/handlers/" in path_lower:
            types.add("feat")
            scopes.add("backend-api")
        elif "app/" in path_lower:
            types.add("feat")
            scopes.add("flutter-app")
        else:
            types.add("chore")

    if "feat" in types:
        commit_type = "feat"
    elif "fix" in types:
        commit_type = "fix"
    elif "refactor" in types:
        commit_type = "refactor"
    elif "test" in types and len(types) == 1:
        commit_type = "test"
    elif "docs" in types and len(types) == 1:
        commit_type = "docs"
    elif "build" in types and len(types) == 1:
        commit_type = "build"
    else:
        commit_type = "feat"

    if len(scopes) == 1:
        commit_scope = list(scopes)[0]
    elif "backend-shared" in scopes or "backend-api" in scopes:
        commit_scope = "backend"
    elif "flutter-app" in scopes:
        commit_scope = "app"
    else:
        commit_scope = "core"

    description = f"implement changes across {len(changed_files)} file(s)"
    if changed_files:
        first_basename = os.path.basename(changed_files[0])
        description = f"implement {first_basename} and related changes"

    return commit_type, commit_scope, description


def main():
    # 檢查 git 變更狀態
    status_output = run_cmd(["git", "status", "--porcelain"])
    if not status_output:
        print(json.dumps({"decision": "allow", "message": "No changes to commit"}))
        sys.exit(0)

    changed_files = []
    for line in status_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            changed_files.append(parts[1])

    if not changed_files:
        print(json.dumps({"decision": "allow", "message": "No files changed"}))
        sys.exit(0)

    # 依據變更內容分析 Convention
    c_type, c_scope, c_desc = analyze_commit_info(changed_files)
    commit_msg = f"{c_type}({c_scope}): {c_desc}"

    # Stage 所有變更
    run_cmd(["git", "add", "."])

    # 執行 Git Commit
    commit_res = run_cmd(["git", "commit", "-m", commit_msg])

    result = {
        "decision": "allow",
        "message": f"Successfully committed: {commit_msg}",
        "detail": commit_res
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
