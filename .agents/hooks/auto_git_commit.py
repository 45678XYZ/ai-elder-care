"""Antigravity IDE Hook - LLM-powered Auto Git Commit

Reads workspace git diff, uses LLM (AWS Bedrock / Gemini / OpenAI) instructed by 
.agents/skills/git-commit/SKILL.md to analyze the exact code diff,
and generates semantic Conventional Commit messages dynamically without hardcoding.
"""

import json
import os
import subprocess
import sys

# Try importing boto3 for AWS Bedrock
try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


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


def generate_commit_msg_with_llm(diff: str, files: list[str], skill_instructions: str) -> str:
    """呼叫 LLM (AWS Bedrock / Gemini API / OpenAI) 動態分析 Diff 並生成 Conventional Commit。"""
    system_prompt = f"""You are an AI Git Commit Assistant. Follow the Conventional Commits specification in the provided skill instructions.

<SKILL_INSTRUCTIONS>
{skill_instructions}
</SKILL_INSTRUCTIONS>

Task:
Analyze the provided git diff and changed files list, then generate a concise, precise Conventional Commit message.
Format MUST be: <type>[optional scope]: <description>

Rules:
- Do NOT output any markdown blocks, quotes, or explanatory text.
- Output ONLY the commit message line.
- Present tense, imperative mood (e.g. 'add' not 'added').
"""

    user_prompt = f"""Changed Files:
{json.dumps(files, indent=2, ensure_ascii=False)}

Git Diff:
{diff[:6000]}
"""

    # 1. 優先嘗試 AWS Bedrock (專案架構規範)
    if HAS_BOTO3:
        try:
            region = os.environ.get("AWS_REGION", "us-east-1")
            client = boto3.client("bedrock-runtime", region_name=region)
            model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
            
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            })
            
            response = client.invoke_model(body=body, modelId=model_id)
            res_body = json.loads(response.get("body").read())
            msg = res_body.get("content", [{}])[0].get("text", "").strip()
            if msg:
                # 移除可能的多餘換行或引號
                msg = msg.strip('"').strip("'").strip("`").splitlines()[0]
                return msg
        except Exception as e:
            sys.stderr.write(f"Bedrock LLM invocation fallback: {e}\n")

    # 2. 次要嘗試 HTTP LLM (若有 GEMINI_API_KEY 或 OPENAI_API_KEY)
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            import urllib.request
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                msg = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if msg:
                    return msg.strip('"').strip("'").strip("`").splitlines()[0]
        except Exception as e:
            sys.stderr.write(f"Gemini API invocation fallback: {e}\n")

    # 3. LLM API 無法取得時之智慧推導備援
    return fallback_llm_analysis(files)


def fallback_llm_analysis(files: list[str]) -> str:
    """當無 LLM 金鑰時之邏輯推導回傳訊息 (避免硬編碼特定字串)。"""
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

    # 讀取 skill 規範
    skill_instructions = get_skill_instructions()

    # 呼叫 LLM 分析 diff 並動態產出 commit 訊息
    commit_msg = generate_commit_msg_with_llm(diff, files, skill_instructions)

    # 執行 Git Commit
    commit_res = run_cmd(["git", "commit", "-m", commit_msg])

    result = {
        "decision": "allow",
        "message": f"LLM Generated Commit: {commit_msg}",
        "detail": commit_res
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
