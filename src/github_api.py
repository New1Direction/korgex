"""
Korgex GitHub Integration — Enterprise API for PRs, Issues, Comments.

Wraps the GitHub REST API and gh CLI for full repository management.
"""

import os
import subprocess

GITHUB_TOKEN_ENV = "KORGEX_GITHUB_TOKEN"
GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    """Get auth headers for GitHub API."""
    token = os.environ.get(GITHUB_TOKEN_ENV) or os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Korgex/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gh(command: str) -> dict:
    """Run a gh CLI command."""
    try:
        result = subprocess.run(
            ["gh"] + command.split(),
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "GH_TOKEN": os.environ.get(GITHUB_TOKEN_ENV, "")}
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


def create_pr(owner: str, repo: str, title: str, body: str, head: str, base: str = "main") -> dict:
    """Create a pull request."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    r = requests.post(url, headers=_headers(), json={
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    })
    return r.json() if r.status_code == 201 else {"error": r.json(), "status": r.status_code}


def list_prs(owner: str, repo: str, state: str = "open") -> list:
    """List pull requests."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls?state={state}"
    r = requests.get(url, headers=_headers())
    return r.json() if r.status_code == 200 else []


def get_pr_comments(owner: str, repo: str, pr_number: int) -> list:
    """Get comments on a PR."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    r = requests.get(url, headers=_headers())
    return r.json() if r.status_code == 200 else []


def reply_to_pr_comment(owner: str, repo: str, comment_id: int, reply: str) -> dict:
    """Reply to a specific PR comment."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/comments/{comment_id}/replies"
    r = requests.post(url, headers=_headers(), json={"body": reply})
    return r.json() if r.status_code == 201 else {"error": r.json(), "status": r.status_code}


def create_issue(owner: str, repo: str, title: str, body: str = "", labels: list = None) -> dict:
    """Create a GitHub issue."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
    data = {"title": title, "body": body}
    if labels:
        data["labels"] = labels
    r = requests.post(url, headers=_headers(), json=data)
    return r.json() if r.status_code == 201 else {"error": r.json(), "status": r.status_code}


def label_issue(owner: str, repo: str, issue_number: int, labels: list) -> dict:
    """Add labels to an issue."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/labels"
    r = requests.post(url, headers=_headers(), json={"labels": labels})
    return r.json() if r.status_code == 200 else {"error": r.json(), "status": r.status_code}


def get_repo_info(owner: str, repo: str) -> dict:
    """Get repository metadata."""
    import requests
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    r = requests.get(url, headers=_headers())
    return r.json() if r.status_code == 200 else {"error": r.json(), "status": r.status_code}


def init_from_cli():
    """Try to authenticate from gh CLI if no token is set."""
    if not os.environ.get(GITHUB_TOKEN_ENV):
        result = _gh("auth token")
        if result["exit_code"] == 0:
            os.environ[GITHUB_TOKEN_ENV] = result["stdout"].strip()
            return True
    return False