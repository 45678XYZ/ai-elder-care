"""Antigravity IDE Hook - Local LLM-powered Auto Git Commit (Gemma-4-E2B-it via llama.cpp)

Reads workspace git diff, uses lightweight local Gemma-4-E2B-it model via llama.cpp 
instructed by .agents/skills/git-commit/SKILL.md to analyze the exact code diff
and generate a semantic Conventional Commit message dynamically.
Safely releases GPU/RAM memory after inference and explicitly reports execution engine status.
"""

import gc
import json
import os
import subprocess
import sys

# 本地 LLM 模型路徑與包含 llama_cpp 之 Python 環境
LLAMA_PYTHON = r"C:\Users\chent\Desktop\我的資料夾\學校\大學\比賽\aws-hackathon\.venv\Scripts\python.exe"
MODEL_PATH = r"C:\Users\chent\Desktop\我的資料夾\學校\大學\比賽\aws-hackathon\models\gemma-4-E2B-it-Q5_K_M.gguf"

try:
    from llama_cpp import Llama
    HAS_LLAMA_CPP = True
except ImportError:
    HAS_LLAMA_CPP = False
    if os.path.exists(LLAMA_PYTHON) and sys.executable.lower() != LLAMA_PYTHON.lower():
        cmd = [LLAMA_PYTHON] + sys.argv
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        print(res.stdout)
        if res.stderr:
            sys.stderr.write(res.stderr)
        sys.exit(res.returncode)


def run_cmd(args: list[str]) -> str:
    """執行 Shell 指令並回傳 stdout。"""
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


def get_git_diff() -> tuple[str, list[str]]:
    """Stage 當前變更並取得 git diff 與檔案列表。"""
    run_cmd(["git", "add", "."])
    diff = run_cmd(["git", "diff", "--staged"])
    
    status_out = run_cmd(["git", "status", "--porcelain"])
    files = []
    for line in status_out.splitlines():
        line = line.strip()
        if line:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                files.append(parts[1])
                
    return diff, files


def get_skill_instructions() -> str:
    """讀取 .agents/skills/git-commit/SKILL.md 作為 LLM 系統提示詞規範。"""
    skill_path = os.path.join(os.getcwd(), ".agents", "skills", "git-commit", "SKILL.md")
    if os.path.exists(skill_path):
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return "Follow Conventional Commits specification: <type>[optional scope]: <description>"


def load_llama_model(model_path: str):
    """嘗試不同的記憶體與 GPU 參數初始化 Llama 模型實例。"""
    configs = [
        {"n_ctx": 4096, "n_gpu_layers": 99},
        {"n_ctx": 4096, "n_gpu_layers": 16},
        {"n_ctx": 2048, "n_gpu_layers": 0},
    ]
    for cfg in configs:
        try:
            sys.stderr.write(f"Initializing Llama with {cfg}...\n")
            llm = Llama(
                model_path=model_path,
                n_ctx=cfg["n_ctx"],
                n_gpu_layers=cfg["n_gpu_layers"],
                n_threads=8,
                verbose=False
            )
            return llm, cfg
        except Exception as err:
            sys.stderr.write(f"Config {cfg} failed: {err}\n")
    return None, None


def generate_commit_msg_with_gemma_llamacpp(diff: str, files: list[str], skill_instructions: str) -> tuple[str, str, bool, str | None]:
    """使用本地 Gemma-4-E2B-it (llama.cpp) 分析 git diff 並動態生成 Conventional Commit。

    回傳: (commit_msg, engine_name, is_fallback, error_reason)
    """
    if not os.path.exists(MODEL_PATH):
        err_msg = f"Model file not found at {MODEL_PATH}"
        sys.stderr.write(f"{err_msg}\n")
        return fallback_commit_analysis(files), "Fallback Rule Generator", True, err_msg

    user_content = f"""You are a Conventional Commits generator following these skill rules:

<SKILL_RULES>
{skill_instructions}
</SKILL_RULES>

Changed Files:
{json.dumps(files, indent=2, ensure_ascii=False)}

Git Diff:
{diff[:3000]}

Task:
Generate a single-line Conventional Commit message for the changes above.
Format MUST be: <type>[optional scope]: <description>
Requirements:
- Output ONLY the single commit message line. No markdown formatting, quotes, or conversational text.
- Present tense, imperative mood (e.g. 'add' not 'added').
"""

    prompt = f"<bos><start_of_turn>user\n{user_content}<end_of_turn>\n<start_of_turn>model\n"

    llm = None
    last_error = None

    try:
        llm, cfg = load_llama_model(MODEL_PATH)
        if llm is not None:
            output = llm(
                prompt,
                max_tokens=64,
                temperature=0.2,
                stop=["<end_of_turn>", "<eos>", "<turn|>", "\n"]
            )
            
            raw_text = output["choices"][0]["text"].strip()
            msg = raw_text.strip('"').strip("'").strip("`").strip()
            if msg:
                engine_name = f"Gemma-4-E2B-it (llama.cpp, n_gpu_layers={cfg['n_gpu_layers']}, n_ctx={cfg['n_ctx']})"
                return msg, engine_name, False, None
    except Exception as e:
        last_error = str(e)
        sys.stderr.write(f"llama.cpp execution error: {e}\n")
    finally:
        # 安全性記憶體/顯存釋放 (Memory Cleanup)
        if llm is not None:
            sys.stderr.write("Cleaning up Llama instance and releasing GPU/RAM memory...\n")
            try:
                if hasattr(llm, "close"):
                    llm.close()
            except Exception:
                pass
            del llm
            gc.collect()

    fallback_reason = last_error if last_error else "Llama context creation failed across all configurations"
    return fallback_commit_analysis(files), "Fallback Rule Generator", True, fallback_reason


def fallback_commit_analysis(files: list[str]) -> str:
    """備援 Commit 訊息推導。"""
    if not files:
        return "chore: update project files"
    first_file = files[0]
    ext = os.path.splitext(first_file)[1]
    if "docs/" in first_file or ext == ".md":
        return f"docs: update {os.path.basename(first_file)}"
    elif "test" in first_file:
        return f"test: update {os.path.basename(first_file)}"
    elif "terraform/" in first_file or ext in (".tf", ".hcl"):
        return f"build(infra): update {os.path.basename(first_file)}"
    elif "src/" in first_file:
        return f"feat(backend): update {os.path.basename(first_file)}"
    else:
        return f"chore: update {os.path.basename(first_file)}"


def main():
    diff, files = get_git_diff()
    if not files:
        print(json.dumps({"decision": "allow", "message": "No changes to commit"}))
        sys.exit(0)

    skill_instructions = get_skill_instructions()
    commit_msg, engine, is_fallback, fallback_reason = generate_commit_msg_with_gemma_llamacpp(diff, files, skill_instructions)

    commit_res = run_cmd(["git", "commit", "-m", commit_msg])

    result = {
        "decision": "allow",
        "message": f"Generated Commit [{engine}]: {commit_msg}",
        "engine": engine,
        "is_fallback": is_fallback,
        "fallback_reason": fallback_reason,
        "detail": commit_res
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
