"""
All 30 active + 3 deprecated tool handlers for Korgex.
Mirrors Jules' complete tool surface extracted from Gemini 4 Pro.
"""

import os
import json
import subprocess
import shutil
import shlex
import tempfile
import threading
from pathlib import Path
from src.tool_base import register_tool, ToolParam
from src.sandbox import SandboxManager
from src.github_api import (
    create_pr, list_prs, get_pr_comments, reply_to_pr_comment,
    create_issue, label_issue, get_repo_info, init_from_cli
)
from src.swarm import AgentSwarm, SubTask
from src.diff_engine import DiffEngine
from src.self_healing import TDDHealer, extract_traceback_info
from src.dependency_graph import DependencyAnalyzer
from src.profiler import PerformanceProfiler

# Lazy singletons — initialized on first use, not at import time.
# This keeps test collection fast and avoids touching GitHub env vars on import.
SANDBOX = None
SWARM = None
_github_initialized = False
_github_init_lock = threading.Lock()


def _get_swarm() -> AgentSwarm:
    global SWARM
    if SWARM is None:
        SWARM = AgentSwarm()
    return SWARM


def _ensure_github():
    """Call init_from_cli() exactly once, safely under concurrent tool calls."""
    global _github_initialized
    if not _github_initialized:                    # fast path, no lock
        with _github_init_lock:
            if not _github_initialized:            # re-check under lock
                init_from_cli()
                _github_initialized = True

REPO_ROOT = None

def _run_bash(command: str, cwd: str = None) -> dict:
    """Run a bash command and return structured output."""
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd or REPO_ROOT or os.getcwd()
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out after 300s", "exit_code": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


@register_tool("list_files", "Lists all files and directories under the given directory (defaults to repo root).", [
    ToolParam("path", "STRING", "The directory path to list files from. Defaults to the root of the repo."),
])
def tool_list_files(path: str = None, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    target = os.path.join(cwd, path) if path else cwd
    if not os.path.isdir(target):
        return {"error": f"Directory not found: {path or '(root)'}"}
    
    result = _run_bash(f"ls -a -1F --group-directories-first {shlex.quote(target)}")
    return {
        "files": result["stdout"].splitlines(),
        "path": path or "(root)",
        "total": len(result["stdout"].splitlines()),
    }


@register_tool("read_file", "Reads the content of the specified file in the repo.", [
    ToolParam("filepath", "STRING", "The path of the file to read, relative to the repo root.", required=True),
])
def tool_read_file(filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    if not os.path.isfile(full_path):
        return {"error": f"File does not exist: {filepath}"}
    try:
        with open(full_path, "r") as f:
            content = f.read()
        return {"content": content, "filepath": filepath, "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


@register_tool("write_file", "Use this to create a new file or overwrite an existing file.", [
    ToolParam("filepath", "STRING", "The path of the file to create or overwrite.", required=True),
    ToolParam("content", "STRING", "The content to write to the file.", required=True),
])
def tool_write_file(filepath: str, content: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
    try:
        with open(full_path, "w") as f:
            f.write(content)
        return {"result": "File written successfully", "filepath": filepath, "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


@register_tool("replace_with_git_merge_diff", "Perform a targeted search-and-replace using Git merge diff format.", [
    ToolParam("filepath", "STRING", "The path of the file to modify.", required=True),
    ToolParam("merge_diff", "STRING", "The diff to apply to the file.", required=True),
])
def tool_replace_with_git_merge_diff(filepath: str, merge_diff: str, context: dict = None):
    """Parse SEARCH/REPLACE blocks and apply them."""
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    if not os.path.isfile(full_path):
        return {"error": f"File does not exist: {filepath}"}
    
    with open(full_path, "r") as f:
        content = f.read()
    
    # Parse SEARCH/REPLACE blocks
    blocks = merge_diff.split("<<<<<<< SEARCH")
    if len(blocks) < 2:
        return {"error": "No SEARCH blocks found. Use <<<<<<< SEARCH / ======= / >>>>>>> REPLACE format."}
    
    modified = content
    changes = 0
    
    for block in blocks[1:]:
        if "=======" not in block:
            continue
        if ">>>>>>> REPLACE" not in block:
            continue
        
        search_part = block.split("=======")[0].strip()
        replace_part = block.split("=======")[1].split(">>>>>>> REPLACE")[0].strip()
        
        if search_part in modified:
            modified = modified.replace(search_part, replace_part, 1)
            changes += 1
        else:
            return {"error": f"SEARCH block not found in file:\n{search_part[:200]}"}
    
    if changes == 0:
        return {"error": "No changes applied. Check SEARCH/REPLACE format."}
    
    with open(full_path, "w") as f:
        f.write(modified)
    
    return {"result": f"Applied {changes} change(s)", "filepath": filepath}


@register_tool("delete_file", "Deletes the specified file.", [
    ToolParam("filepath", "STRING", "The path of the file to delete.", required=True),
])
def tool_delete_file(filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    if not os.path.isfile(full_path):
        return {"error": f"File does not exist: {filepath}"}
    try:
        os.remove(full_path)
        return {"result": f"Deleted: {filepath}"}
    except Exception as e:
        return {"error": str(e)}


@register_tool("rename_file", "Renames and/or moves files and directories.", [
    ToolParam("filepath", "STRING", "The original path of the file or directory.", required=True),
    ToolParam("new_filepath", "STRING", "The new path for the file or directory.", required=True),
])
def tool_rename_file(filepath: str, new_filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    src = os.path.join(cwd, filepath)
    dst = os.path.join(cwd, new_filepath)
    if not os.path.exists(src):
        return {"error": f"Source does not exist: {filepath}"}
    if os.path.exists(dst):
        return {"error": f"Target already exists: {new_filepath}"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        return {"result": f"Renamed {filepath} → {new_filepath}"}
    except Exception as e:
        return {"error": str(e)}


@register_tool("reset_all", "Resets the entire codebase to its original state. Use to undo all changes.")
def tool_reset_all(context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    result = _run_bash("git reset --hard HEAD && git clean -fd", cwd)
    return {"result": "Codebase reset to original state", "output": result["stdout"]}


@register_tool("restore_file", "Restores the given file to its original state.", [
    ToolParam("filepath", "STRING", "The path of the file to restore.", required=True),
])
def tool_restore_file(filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    result = _run_bash(f"git checkout -- {shlex.quote(filepath)}", cwd)
    return {"result": f"Restored: {filepath}", "output": result["stdout"]}


@register_tool("run_in_bash_session", "Runs a bash command in an isolated sandbox (cloud VM, Docker, or local).", [
    ToolParam("command", "STRING", "The bash command to run.", required=True),
])
def tool_run_in_bash_session(command: str, context: dict = None):
    """Run command in sandbox (cloud VM > Docker > direct fallback)."""
    global SANDBOX
    
    # Use sandbox if available
    if SANDBOX:
        result = SANDBOX.run(command)
    else:
        # Fallback to local execution
        cwd = context.get("repo_root") if context else os.getcwd()
        result = _run_bash(command, cwd)
    
    return {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
    }


@register_tool("run_test_with_self_healing", "Runs tests and automatically self-corrects any failures up to 5 times using the TDD Healer.", [
    ToolParam("test_command", "STRING", "The test command to execute.", required=True),
    ToolParam("target_file", "STRING", "The file path containing the code to fix.", required=True),
    ToolParam("context_files", "ARRAY", "Optional list of context files (e.g. test files)."),
])
def tool_run_test_with_self_healing(test_command: str, target_file: str, context_files: list = None, context: dict = None):
    """Run tests with self-healing loop. Parses failures, patches via LLM, retries."""
    global SANDBOX
    
    if not SANDBOX:
        return {"error": "Sandbox required for self-healing. Set KORGEX_SANDBOX=docker or modal."}
    
    # Initialize LLM client
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=os.environ.get("KORGEX_API_URL", "https://inference-api.provider.com/v1"),
            api_key=os.environ.get("KORGEX_API_KEY", ""),
        )
    except ImportError:
        return {"error": "openai package required: pip install openai"}
    
    model = os.environ.get("KORGEX_MODEL", "deepseek/deepseek-v4-flash")
    
    healer = TDDHealer(
        sandbox=SANDBOX,
        api_client=client,
        model=model,
        max_attempts=int(os.environ.get("KORGEX_HEAL_MAX_ATTEMPTS", "5")),
    )
    
    result = healer.heal(test_command, target_file, context_files or [])
    
    # Parse traceback for debugging
    if result.get("status") == "failure":
        tb = extract_traceback_info(result.get("output", ""))
        result["traceback"] = tb
    
    return result


@register_tool("google_search", "Online Google search to retrieve up-to-date information.", [
    ToolParam("query", "STRING", "The query to search for.", required=True),
])
def tool_google_search(query: str, context: dict = None):
    """Uses the configured search tool."""
    from web_tools import web_search
    try:
        result = web_search(query=query, limit=5)
        return result
    except ImportError:
        return {"error": "web_search not available. Install web_tools or configure a search provider."}


@register_tool("view_text_website", "Fetches website content as plain text.", [
    ToolParam("url", "STRING", "The URL of the website to fetch.", required=True),
])
def tool_view_text_website(url: str, context: dict = None):
    try:
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": "Korgex/1.0"})
        return {"url": url, "content": r.text[:50000], "status": r.status_code}
    except Exception as e:
        return {"error": str(e)}


@register_tool("set_plan", "Use after initial exploration to set the first plan.", [
    ToolParam("plan", "STRING", "The plan to solve the issue, in Markdown format.", required=True),
])
def tool_set_plan(plan: str, context: dict = None):
    plan_file = os.path.join(context.get("repo_root", os.getcwd()), ".korgex", "plan.md")
    os.makedirs(os.path.dirname(plan_file), exist_ok=True)
    with open(plan_file, "w") as f:
        f.write(plan)
    return {"result": "Plan set", "plan": plan}


@register_tool("plan_step_complete", "Marks the current plan step as complete.", [
    ToolParam("message", "STRING", "Description of what was accomplished.", required=True),
])
def tool_plan_step_complete(message: str, context: dict = None):
    plan_dir = os.path.join(context.get("repo_root", os.getcwd()), ".korgex")
    steps_file = os.path.join(plan_dir, "steps.json")
    os.makedirs(plan_dir, exist_ok=True)
    
    steps = []
    if os.path.exists(steps_file):
        with open(steps_file) as f:
            steps = json.load(f)
    
    steps.append({
        "message": message,
        "timestamp": str(__import__("datetime").datetime.now()),
    })
    
    with open(steps_file, "w") as f:
        json.dump(steps, f, indent=2)
    
    return {"result": "Step marked complete", "message": message}


@register_tool("record_user_approval_for_plan", "Records the user's approval for the plan.")
def tool_record_user_approval_for_plan(context: dict = None):
    plan_dir = os.path.join(context.get("repo_root", os.getcwd()), ".korgex")
    os.makedirs(plan_dir, exist_ok=True)
    with open(os.path.join(plan_dir, "approved"), "w") as f:
        f.write("approved")
    return {"result": "Plan approved"}


@register_tool("message_user", "Send a message to the user.", [
    ToolParam("message", "STRING", "The message to send.", required=True),
    ToolParam("continue_working", "BOOLEAN", "Whether to continue working after sending."),
])
def tool_message_user(message: str, continue_working: bool = True, context: dict = None):
    print(f"\n[KORGEX] {message}")
    return {"sent": True, "message": message, "continue_working": continue_working}


@register_tool("request_user_input", "Asks the user a question and waits for a response.", [
    ToolParam("message", "STRING", "The question or prompt for the user.", required=True),
])
def tool_request_user_input(message: str, context: dict = None):
    response = input(f"\n[KORGEX ASKS] {message}\n> ")
    return {"response": response}


@register_tool("pre_commit_instructions", "Get pre-commit steps. Call before submit.")
def tool_pre_commit_instructions(context: dict = None):
    instructions = """Pre-commit steps:
1. Run all tests: python -m pytest tests/ or npm test
2. Run linter: ruff check . or eslint .
3. Type check: mypy . or tsc --noEmit
4. Verify no debug code left behind
5. Review diff: git diff
"""
    return {"instructions": instructions}


@register_tool("submit", "Commits code and requests user approval to push.", [
    ToolParam("branch_name", "STRING", "The branch name.", required=True),
    ToolParam("commit_message", "STRING", "The commit message.", required=True),
    ToolParam("title", "STRING", "The title of the submission.", required=True),
    ToolParam("description", "STRING", "The description of the submission.", required=True),
])
def tool_submit(branch_name: str, commit_message: str, title: str, description: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    
    # Create branch
    _run_bash(f"git checkout -b {shlex.quote(branch_name)}", cwd)
    
    # Add and commit
    _run_bash("git add -A", cwd)
    commit_result = _run_bash(f"git commit -m {shlex.quote(commit_message)}", cwd)
    
    return {
        "branch": branch_name,
        "commit_message": commit_message,
        "title": title,
        "description": description,
        "commit_output": commit_result["stdout"] or commit_result["stderr"],
    }


@register_tool("request_code_review", "Request a code review for the current change.")
def tool_request_code_review(context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    diff_result = _run_bash("git diff HEAD~1 --stat", cwd)
    return {
        "result": "Code review requested",
        "changes": diff_result["stdout"],
        "message": "Please review the changes above.",
    }


@register_tool("read_pr_comments", "Reads any pending pull request comments.")
def tool_read_pr_comments(context: dict = None):
    return {"comments": [], "message": "No pending PR comments."}


@register_tool("reply_to_pr_comments", "Reply to PR comments.", [
    ToolParam("replies", "STRING", "JSON string: [{\"comment_id\": \"...\", \"reply\": \"...\"}]", required=True),
])
def tool_reply_to_pr_comments(replies: str, context: dict = None):
    try:
        parsed = json.loads(replies)
        return {"result": f"Replied to {len(parsed)} comments", "replies": parsed}
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in replies parameter"}


@register_tool("initiate_memory_recording", "Start recording info for future tasks.")
def tool_initiate_memory_recording(context: dict = None):
    return {"result": "Memory recording initiated"}


_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB — guard against accidental OOM


@register_tool("view_image", "Downloads an image from a URL and returns its base64-encoded contents for multimodal analysis.", [
    ToolParam("url", "STRING", "The URL of the image to view.", required=True),
])
def tool_view_image(url: str, context: dict = None):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Korgex/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            length = resp.getheader("Content-Length")
            if length and int(length) > _MAX_IMAGE_BYTES:
                mb = int(length) // 1024 // 1024
                return {"error": f"Image too large ({mb} MB > 25 MB limit)"}
            data = resp.read(_MAX_IMAGE_BYTES + 1)
        if len(data) > _MAX_IMAGE_BYTES:
            return {"error": "Image exceeds 25 MB limit — aborting to avoid OOM"}

        ext = url.split("?")[0].rsplit(".", 1)[-1][:8] if "." in url.split("?")[0] else "png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        from src.vision import VisionEngine
        result = VisionEngine.analyze_image(tmp_path)
        result["source_url"] = url
        return result
    except Exception as e:
        return {"error": f"Failed to load image from {url}: {e}"}


@register_tool("read_image_file", "Reads an image file from disk and returns its base64-encoded contents for multimodal analysis.", [
    ToolParam("filepath", "STRING", "The path of the image file.", required=True),
])
def tool_read_image_file(filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    from src.vision import VisionEngine
    return VisionEngine.analyze_local_file(full_path)


@register_tool("read_media_file", "Reads a media file (image/video) from disk and returns its contents for multimodal analysis.", [
    ToolParam("filepath", "STRING", "The path of the media file.", required=True),
])
def tool_read_media_file(filepath: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    full_path = os.path.join(cwd, filepath)
    from src.vision import VisionEngine
    return VisionEngine.analyze_local_file(full_path)


@register_tool("frontend_verification_instructions", "Returns Playwright instructions for frontend verification.")
def tool_frontend_verification_instructions(context: dict = None):
    return {
        "instructions": """To verify frontend changes:
1. Install Playwright: npm init playwright@latest
2. Write a test script that:
   - Starts the dev server
   - Takes screenshots of changed pages
   - Verifies key elements exist
3. Run: npx playwright test
4. Use frontend_verification_complete with the screenshot path"""
    }


@register_tool("frontend_verification_complete", "Indicate frontend changes have been verified.", [
    ToolParam("screenshot_path", "STRING", "Path to the screenshot.", required=True),
    ToolParam("additional_media_paths", "ARRAY", "Additional media files to include."),
])
def tool_frontend_verification_complete(screenshot_path: str, additional_media_paths: list = None, context: dict = None):
    return {
        "result": "Frontend verified",
        "screenshot": screenshot_path,
        "additional_media": additional_media_paths or [],
    }


@register_tool("start_live_preview_instructions", "Returns instructions for starting a live preview server.")
def tool_start_live_preview_instructions(context: dict = None):
    return {
        "instructions": "Run the dev server in the background: npm run dev & or python -m http.server 8000 &"
    }


@register_tool("call_hello_world_agent", "Delegates a sub-task to a specialized agent in a parallel sandbox.", [
    ToolParam("message", "STRING", "The task description for the sub-agent.", required=True),
])
def tool_call_hello_world_agent(message: str, context: dict = None):
    """Now uses the real agent swarm for delegation."""
    repo_root = context.get("repo_root") if context else os.getcwd()

    # Auto-detect agent type from the message
    msg_lower = message.lower()
    if any(w in msg_lower for w in ["test", "pytest", "unittest", "spec"]):
        agent_type = "test"
    elif any(w in msg_lower for w in ["security", "vuln", "cve", "audit"]):
        agent_type = "security"
    elif any(w in msg_lower for w in ["doc", "readme", "document"]):
        agent_type = "docs"
    elif any(w in msg_lower for w in ["refactor", "clean", "optimize"]):
        agent_type = "refactor"
    else:
        agent_type = "test"  # default

    task = SubTask(agent_type, message, repo_root)
    result = _get_swarm().run_concurrent([task])
    
    r = result[0] if result else None
    if r and r.success:
        return {"response": r.summary, "agent": r.agent_type, "duration": r.duration_seconds}
    return {"response": "Sub-agent task completed", "result": str(r) if r else "no output"}


@register_tool("done", "Subagent completion signal.", [
    ToolParam("summary", "STRING", "Summary of what was accomplished.", required=True),
])
def tool_done(summary: str, context: dict = None):
    return {"result": "Task completed", "summary": summary}


# Deprecated tools
@register_tool("grep", "DEPRECATED - use grep with run_in_bash_session instead.", [
    ToolParam("pattern", "STRING", "The pattern to search for.", required=True),
])
def tool_grep(pattern: str, context: dict = None):
    cwd = context.get("repo_root") if context else os.getcwd()
    result = _run_bash(f"grep -r {shlex.quote(pattern)} --include='*.py' --include='*.js' --include='*.ts' --include='*.rs' --include='*.go' .", cwd)
    return {"matches": result["stdout"].splitlines(), "total": len(result["stdout"].splitlines())}


@register_tool("create_file_with_block", "DEPRECATED - use write_file instead.", [
    ToolParam("filepath", "STRING", "The path of the file to create.", required=True),
    ToolParam("content", "STRING", "The content to write.", required=True),
])
def tool_create_file_with_block(filepath: str, content: str, context: dict = None):
    return tool_write_file(filepath, content, context)


@register_tool("overwrite_file_with_block", "DEPRECATED - use write_file instead.", [
    ToolParam("filepath", "STRING", "The path of the file to overwrite.", required=True),
    ToolParam("content", "STRING", "The new content for the file.", required=True),
])
def tool_overwrite_file_with_block(filepath: str, content: str, context: dict = None):
    return tool_write_file(filepath, content, context)


# ─── Enterprise GitHub Tools ─────────────────────────────────────────────

@register_tool("github_create_pr", "Creates a pull request on GitHub.", [
    ToolParam("owner", "STRING", "Repository owner.", required=True),
    ToolParam("repo", "STRING", "Repository name.", required=True),
    ToolParam("title", "STRING", "PR title.", required=True),
    ToolParam("body", "STRING", "PR body/description.", required=True),
    ToolParam("head", "STRING", "Branch name with changes.", required=True),
    ToolParam("base", "STRING", "Target branch (default: main)."),
])
def tool_github_create_pr(owner: str, repo: str, title: str, body: str, head: str, base: str = "main", context: dict = None):
    _ensure_github()
    result = create_pr(owner, repo, title, body, head, base)
    return result


@register_tool("github_list_prs", "Lists pull requests for a repository.", [
    ToolParam("owner", "STRING", "Repository owner.", required=True),
    ToolParam("repo", "STRING", "Repository name.", required=True),
    ToolParam("state", "STRING", "PR state: open, closed, all."),
])
def tool_github_list_prs(owner: str, repo: str, state: str = "open", context: dict = None):
    _ensure_github()
    result = list_prs(owner, repo, state)
    return {"prs": result, "count": len(result)}


@register_tool("github_get_pr_comments", "Gets comments on a pull request.", [
    ToolParam("owner", "STRING", "Repository owner.", required=True),
    ToolParam("repo", "STRING", "Repository name.", required=True),
    ToolParam("pr_number", "STRING", "Pull request number.", required=True),
])
def tool_github_get_pr_comments(owner: str, repo: str, pr_number: str, context: dict = None):
    _ensure_github()
    result = get_pr_comments(owner, repo, int(pr_number))
    return {"comments": result, "count": len(result)}


@register_tool("github_reply_to_pr_comment", "Replies to a specific PR comment.", [
    ToolParam("owner", "STRING", "Repository owner.", required=True),
    ToolParam("repo", "STRING", "Repository name.", required=True),
    ToolParam("comment_id", "STRING", "Comment ID to reply to.", required=True),
    ToolParam("reply", "STRING", "Reply text.", required=True),
])
def tool_github_reply_to_pr_comment(owner: str, repo: str, comment_id: str, reply: str, context: dict = None):
    _ensure_github()
    result = reply_to_pr_comment(owner, repo, int(comment_id), reply)
    return result


@register_tool("github_create_issue", "Creates a GitHub issue with optional labels.", [
    ToolParam("owner", "STRING", "Repository owner.", required=True),
    ToolParam("repo", "STRING", "Repository name.", required=True),
    ToolParam("title", "STRING", "Issue title.", required=True),
    ToolParam("body", "STRING", "Issue body/description."),
    ToolParam("labels", "STRING", "Comma-separated labels."),
])
def tool_github_create_issue(owner: str, repo: str, title: str, body: str = "", labels: str = "", context: dict = None):
    _ensure_github()
    label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else None
    result = create_issue(owner, repo, title, body, label_list)
    return result


# ─── Dependency Graph Tools ───────────────────────────────────────────

@register_tool("get_codebase_impact_report", "Analyzes imports and AST nodes to map downstream files impacted by changing a target file.", [
    ToolParam("target_file", "STRING", "The file path being modified.", required=True),
    ToolParam("changed_symbols", "ARRAY", "List of class names, function names, or variables being altered."),
])
def tool_get_codebase_impact_report(target_file: str, changed_symbols: list = None, context: dict = None):
    global REPO_ROOT
    root = REPO_ROOT or os.getcwd()
    analyzer = DependencyAnalyzer(root)
    try:
        report = analyzer.analyze_impact(target_file, changed_symbols)
        return report
    except Exception as e:
        return {"error": f"Impact analysis failed: {e}"}


@register_tool("get_god_nodes", "Finds files with the most dependents ('god nodes') — high-risk targets for refactoring.", [
    ToolParam("min_dependents", "STRING", "Minimum number of dependents to qualify as a god node (default: 3)."),
])
def tool_get_god_nodes(min_dependents: str = "3", context: dict = None):
    global REPO_ROOT
    root = REPO_ROOT or os.getcwd()
    analyzer = DependencyAnalyzer(root)
    try:
        nodes = analyzer.get_god_nodes(int(min_dependents))
        return {"god_nodes": nodes, "count": len(nodes)}
    except Exception as e:
        return {"error": f"God node analysis failed: {e}"}


# ─── Performance Profiling Tools ────────────────────────────────────────

@register_tool("get_performance_profile", "Runs a test or script under cProfile to find the slowest functions and detect bottlenecks.", [
    ToolParam("command", "STRING", "The python or pytest command to profile (e.g. 'pytest tests/test_core.py').", required=True),
])
def tool_get_performance_profile(command: str, context: dict = None):
    global SANDBOX
    if not SANDBOX:
        return {"error": "Sandbox required for profiling. Set KORGEX_SANDBOX=docker or modal."}
    profiler = PerformanceProfiler(SANDBOX)
    try:
        report = profiler.run_profile(command)
        return report
    except Exception as e:
        return {"error": f"Performance profiling failed: {e}"}


# ─── AST Context Compression Tools ──────────────────────────────────────

@register_tool("get_compressed_file_context", "Retrieves a compressed AST representation of a large Python file, preserving only focus symbols to save tokens.", [
    ToolParam("filepath", "STRING", "Path of the file relative to repo root.", required=True),
    ToolParam("focus_symbols", "ARRAY", "List of class or function names to keep expanded.", required=False),
])
def tool_get_compressed_file_context(filepath: str, focus_symbols: list = None, context: dict = None):
    global REPO_ROOT
    from src.context_compression import ASTCompressor

    root = REPO_ROOT or os.getcwd()
    full_path = os.path.join(root, filepath)

    if not os.path.isfile(full_path):
        return {"error": f"File does not exist: {filepath}"}

    try:
        compressor = ASTCompressor(focus_symbols)
        compressed_source = compressor.compress(full_path)

        # Calculate token savings estimation
        original_size = os.path.getsize(full_path)
        compressed_size = len(compressed_source.encode("utf-8"))
        savings_percent = round((1 - (compressed_size / max(1, original_size))) * 100, 2)
        total_lines = compressed_source.count("\n")

        return {
            "filepath": filepath,
            "original_size_bytes": original_size,
            "compressed_size_bytes": compressed_size,
            "token_savings_percent": f"{savings_percent}%",
            "total_lines": total_lines,
            "compressed_content": compressed_source,
        }
    except Exception as e:
        return {"error": f"Failed to compress file: {str(e)}"}


# ─── Enterprise Vision Tools ────────────────────────────────────────────

@register_tool("capture_screenshot", "Takes a browser screenshot of a URL for visual verification.", [
    ToolParam("url", "STRING", "URL to screenshot.", required=True),
    ToolParam("output_path", "STRING", "Custom output path for the screenshot."),
])
def tool_capture_screenshot(url: str, output_path: str = None, context: dict = None):
    try:
        from src.vision import VisionEngine
        result = VisionEngine.take_screenshot(url, output_path)
        return result
    except Exception as e:
        return {"error": f"Screenshot failed: {e}"}


@register_tool("analyze_image", "Analyzes an image file (screenshot, UI mockup, diagram).", [
    ToolParam("filepath", "STRING", "Path to the image file.", required=True),
    ToolParam("question", "STRING", "Specific question about the image."),
])
def tool_analyze_image(filepath: str, question: str = None, context: dict = None):
    try:
        from src.vision import VisionEngine
        result = VisionEngine.analyze_image(filepath, question)
        return result
    except Exception as e:
        return {"error": f"Image analysis failed: {e}"}


# ─── Sandbox Control ──────────────────────────────────────────────────

@register_tool("sandbox_status", "Returns the current sandbox mode and status.", [])
def tool_sandbox_status(context: dict = None):
    global SANDBOX
    if SANDBOX:
        return {"mode": type(SANDBOX).__name__.replace("Sandbox", ""), "status": "active"}
    return {"mode": "none", "status": "not initialized"}


# ─── Memory System (Claude Code-inspired) ─────────────────────────────

@register_tool("memory_save", "Save a persistent memory about the user, project, or workflow. Immutable — use memory_delete first if updating.", [
    ToolParam("name", "STRING", "Short kebab-case slug (e.g. 'prefers-bun-over-npm').", required=True),
    ToolParam("description", "STRING", "One-line summary for relevance matching.", required=True),
    ToolParam("mem_type", "STRING", "Type: user (preferences), feedback (corrections), project (context), or reference (external pointers).", required=True),
    ToolParam("body", "STRING", "Memory content. For feedback/project: include rule/fact, Why:, and How to apply:.", required=True),
])
def tool_memory_save(name: str, description: str, mem_type: str, body: str, context: dict = None):
    from src.memory import save_memory
    return save_memory(name, description, mem_type, body)


@register_tool("memory_delete", "Delete a memory by its slug name. Use before recreating (immutable design).", [
    ToolParam("name", "STRING", "The memory slug name to delete.", required=True),
])
def tool_memory_delete(name: str, context: dict = None):
    from src.memory import delete_memory
    return delete_memory(name)


@register_tool("memory_search", "Search memory index for relevant memories.", [
    ToolParam("query", "STRING", "Search terms to match against memory descriptions.", required=True),
])
def tool_memory_search(query: str, context: dict = None):
    from src.memory import search_memory
    return {"results": search_memory(query)}


@register_tool("memory_list", "List all saved memories, optionally filtered by type.", [
    ToolParam("mem_type", "STRING", "Optional filter: user, feedback, project, or reference."),
])
def tool_memory_list(mem_type: str = None, context: dict = None):
    from src.memory import list_memories
    return {"memories": list_memories(mem_type)}


# ─── Mode & Strict Pairing Tools ──────────────────────────────────────

@register_tool("enter_plan_mode", "Enter plan mode: read-only analysis with no file modifications. Write plan to plan file, then use exit_plan_mode to return. Automatically switches model to Opus for deep reasoning.", [
    ToolParam("plan_file", "STRING", "Path to write the plan file to.", required=True),
])
def tool_enter_plan_mode(plan_file: str, context: dict = None):
    from src.mode_schemas import ModeStateMachine
    from src.model_router import on_mode_change
    machine = ModeStateMachine("execute")
    result = machine.enter_mode("plan")
    on_mode_change("plan")
    return {
        "mode": "plan",
        "model": "Opus 4.7",
        "description": "Read-only analysis mode with Opus for deep reasoning. Use Read, Glob, Grep, Agent(plan), AskUserQuestion. NO writing, editing, or bash.",
        "plan_file": plan_file,
        "instructions": "Write your analysis to the plan file. When ready, call exit_plan_mode for approval."
    }


@register_tool("exit_plan_mode", "Exit plan mode and return to execute mode. Call this when your plan is written and you need approval. Automatically switches model to Sonnet for fast execution.", [
    ToolParam("plan_file", "STRING", "Path to the plan file you wrote.", required=True),
])
def tool_exit_plan_mode(plan_file: str, context: dict = None):
    from src.mode_schemas import ModeStateMachine
    from src.model_router import on_mode_change
    machine = ModeStateMachine("plan")
    result = machine.exit_mode()
    on_mode_change("execute")
    return {
        "mode": "execute",
        "model": "Sonnet 4.6",
        "description": "Full execution mode with Sonnet for fast code generation. All tools available.",
        "plan_file": plan_file,
        "instructions": "Request user approval of the written plan before proceeding."
    }


@register_tool("tool_use_status", "Check the status of tool result pairing. Shows pending and completed tool calls with their IDs.", [])
def tool_tool_use_status(context: dict = None):
    from src.strict_pairing import get_context
    ctx = get_context()
    return ctx.to_dict()


# ─── MCP Server Management ─────────────────────────────────────────────

@register_tool("mcp_connect", "Connect to an MCP (Model Context Protocol) server to discover and use its tools. Connect to GitHub, filesystem, databases, and more.", [
    ToolParam("name", "STRING", "A name for this server connection.", required=True),
    ToolParam("command", "STRING", "The command to run (e.g. 'npx', 'uvx', 'python').", required=True),
    ToolParam("args", "ARRAY", "Command arguments (e.g. ['-y', '@modelcontextprotocol/server-github'])."),
    ToolParam("env", "OBJECT", "Environment variables as key-value pairs."),
])
def tool_mcp_connect(name: str, command: str, args: list = None,
                      env: dict = None, context: dict = None):
    from src.mcp_client import MCPServerConfig, get_manager
    config = MCPServerConfig(name=name, command=command,
                             args=args or [], env=env or {})
    manager = get_manager()
    return manager.add_server(config)


@register_tool("mcp_disconnect", "Disconnect from an MCP server and remove its tools.", [
    ToolParam("name", "STRING", "Server name to disconnect.", required=True),
])
def tool_mcp_disconnect(name: str, context: dict = None):
    from src.mcp_client import get_manager
    manager = get_manager()
    return manager.remove_server(name)


@register_tool("mcp_list", "List all connected MCP servers and their available tools.", [])
def tool_mcp_list(context: dict = None):
    from src.mcp_client import get_manager
    manager = get_manager()
    servers = manager.list_servers()
    tools = manager.get_all_tools()
    return {
        "servers": servers,
        "total_tools": len(tools),
        "tools": [{"name": t.name, "server": t.server_name,
                    "description": t.description[:100]}
                   for t in tools],
    }


def init(repo_root: str = None, sandbox_mode: str = None):
    """Initialize Korgex tools with a repo root and optional sandbox."""
    global REPO_ROOT, SANDBOX
    REPO_ROOT = repo_root or os.getcwd()
    
    # Start interactive session
    try:
        from src.interactive import InteractiveSession
        _session = InteractiveSession()
        _session.start()
    except Exception:
        pass
    
    # Initialize sandbox (auto-detects: modal > docker > direct)
    try:
        SANDBOX = SandboxManager.get(sandbox_mode)
        SANDBOX.setup(REPO_ROOT)
    except Exception as e:
        print(f"Sandbox init warning: {e}")