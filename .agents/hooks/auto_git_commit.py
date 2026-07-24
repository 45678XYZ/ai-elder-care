"""Antigravity IDE Hook - Safety Gate & Auto Git Commit (No External APIs)

1. Runs automated unit tests (pytest) as a safety gate.
2. If tests pass, stages files and generates a Conventional Commit message
   based on changed files according to .agents/skills/git-commit/SKILL.md.
3. Completely free of Gemini API or Bedrock calls.
"""

import json
import os
import subprocess
import sys


def run_cmd(args: list[str], check: bool = True, cwd: str | None = None) -> tuple[int, str, str]:
    """執行 Shell 指令並回傳 (returncode, stdout, stderr)，處理 Windows 編碼。"""
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=check,
            cwd=cwd,
            encoding="utf-8",
            errors="replace"
        )
        stdout = res.stdout.strip() if res.stdout else ""
        stderr = res.stderr.strip() if res.stderr else ""
        return res.returncode, stdout, stderr
    except subprocess.CalledProcessError as e:
        stdout = e.stdout.strip() if e.stdout else ""
        stderr = e.stderr.strip() if e.stderr else str(e)
        return e.returncode, stdout, stderr
    except Exception as e:
        return 1, "", str(e)


def run_tests() -> tuple[bool, str]:
    """執行單元測試 Safety Gate。"""
    backend_dir = os.path.join(os.getcwd(), "backend")
    venv_python = os.path.join(backend_dir, ".venv", "Scripts", "python.exe")
    python_cmd = venv_python if os.path.exists(venv_python) else sys.executable

    test_args = [python_cmd, "-m", "pytest", "-v"]
    code, stdout, stderr = run_cmd(test_args, check=False, cwd=backend_dir)
    
    if code == 0:
        return True, stdout
    else:
        output_msg = stdout if stdout else stderr
        return False, f"Unit tests failed with code {code}:\n{output_msg}"


def analyze_conventional_commit(changed_files: list[str]) -> str:
    """依據 .agents/skills/git-commit/SKILL.md 規範解析 Conventional Commit 訊息。"""
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
        elif "terraform/" in path_lower or path_lower.endswith((".tf", ".hcl")):
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

    # 決定 Type
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

    # 決定 Scope
    if len(scopes) == 1:
        commit_scope = list(scopes)[0]
    elif "backend-shared" in scopes or "backend-api" in scopes:
        commit_scope = "backend"
    elif "flutter-app" in scopes:
        commit_scope = "app"
    else:
        commit_scope = "core"

    first_basename = os.path.basename(changed_files[0]) if changed_files else "files"
    if len(changed_files) == 1:
        desc = f"update {first_basename}"
    else:
        desc = f"update {first_basename} and {len(changed_files) - 1} other file(s)"

    return f"{commit_type}({commit_scope}): {desc}"


def main():
    # 1. 檢查 Git 變更
    _, status_output, _ = run_cmd(["git", "status", "--porcelain"], check=False)
    if not status_output:
        print("No changes to verify or commit.")
        sys.exit(0)

    changed_files = []
    for line in status_output.splitlines():
        line = line.strip()
        if line:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                changed_files.append(parts[1])

    # 2. 執行單元測試 Safety Gate
    print("Running Safety Gate (pytest)...")
    test_passed, test_msg = run_tests()
    if not test_passed:
        sys.stderr.write(f"SAFETY GATE FAILED:\n{test_msg}\n")
        sys.exit(1)  # 阻斷 Commit 並回報 error 給 Antigravity Agent

    print("Safety Gate passed cleanly!")

    # 3. Stage 檔案並生成 Conventional Commit 訊息
    run_cmd(["git", "add", "."])
    commit_msg = analyze_conventional_commit(changed_files)

    # 4. 執行 Commit
    code, out, err = run_cmd(["git", "commit", "-m", commit_msg], check=False)
    if code == 0:
        print(f"Git commit succeeded: {commit_msg}")
    else:
        sys.stderr.write(f"Git commit failed:\n{err if err else out}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
