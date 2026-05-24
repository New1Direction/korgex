"""
KorgKode Webhook Server — GitHub App Daemon.

Listens for GitHub webhooks and triggers KorgKode tasks autonomously.
Designed to run as a 24/7 background service on Modal or a VPS.

Events handled:
- issues.labeled (label = "korgkode") → run task
- issue_comment.created (comment contains "/korgkode") → run task
- pull_request.opened (PR has "korgkode" label) → review PR
- pull_request_review_comment.created (comment contains "/korgkode") → address feedback
"""

import json
import os
import hmac
import hashlib
import subprocess
import threading
from typing import Optional

try:
    from fastapi import FastAPI, Request, HTTPException
    import uvicorn
    WEBHOOK_AVAILABLE = True
except ImportError:
    WEBHOOK_AVAILABLE = False


SECRET = os.environ.get("KORGKODE_WEBHOOK_SECRET", "")
KORGKODE_PATH = os.environ.get("KORGKODE_PATH", os.path.expanduser("~/KorgKode"))


def verify_signature(payload_body: bytes, signature_header: str) -> bool:
    """Verify GitHub webhook signature."""
    if not SECRET:
        return True  # No secret configured — accept all
    expected = "sha256=" + hmac.new(
        SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def create_webhook_app() -> Optional[object]:
    """Create the FastAPI webhook receiver."""
    if not WEBHOOK_AVAILABLE:
        return None
    
    app = FastAPI(title="KorgKode Webhook Server")
    
    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        
        if not verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        event = request.headers.get("X-GitHub-Event", "")
        data = json.loads(body)
        
        # Process in background
        thread = threading.Thread(
            target=_process_webhook,
            args=(event, data),
            daemon=True
        )
        thread.start()
        
        return {"status": "received", "event": event}
    
    @app.get("/health")
    async def health():
        return {"status": "ok", "korgkode": True}
    
    return app


def _process_webhook(event: str, data: dict):
    """Process a GitHub webhook event."""
    try:
        repo = data.get("repository", {}).get("full_name", "unknown")
        clone_url = data.get("repository", {}).get("clone_url", "")
        
        if event == "issues" and data.get("action") == "labeled":
            label = data.get("label", {}).get("name", "")
            if label.lower() == "korgkode":
                issue = data.get("issue", {})
                title = issue.get("title", "")
                body = issue.get("body", "")
                number = issue.get("number", "")
                
                task = f"Issue #{number}: {title}\n{body}"
                _run_korgkode(task, repo, clone_url)
        
        elif event == "issue_comment" and data.get("action") == "created":
            comment = data.get("comment", {}).get("body", "")
            if "/korgkode" in comment.lower():
                issue = data.get("issue", {})
                title = issue.get("title", "")
                number = issue.get("number", "")
                
                task = f"Address comment on issue #{number} ({title}):\n{comment}"
                _run_korgkode(task, repo, clone_url)
        
        elif event == "pull_request" and data.get("action") in ("opened", "labeled"):
            pr = data.get("pull_request", {})
            labels = [l.get("name", "").lower() for l in pr.get("labels", [])]
            
            if "korgkode" in labels:
                title = pr.get("title", "")
                body = pr.get("body", "")
                number = pr.get("number", "")
                head_sha = pr.get("head", {}).get("sha", "")
                
                task = f"Review PR #{number}: {title}\n{body}"
                _run_korgkode(task, repo, clone_url)
        
        elif event == "pull_request_review_comment" and data.get("action") == "created":
            comment = data.get("comment", {}).get("body", "")
            if "/korgkode" in comment.lower():
                task = f"Address review feedback:\n{comment}"
                _run_korgkode(task, repo, clone_url)
    
    except Exception as e:
        print(f"Webhook processing error: {e}")


def _run_korgkode(task: str, repo: str, clone_url: str):
    """Execute KorgKode on a task."""
    print(f"Running KorgKode on {repo}: {task[:80]}...")
    
    workdir = f"/tmp/korgkode-{repo.replace('/', '-')}"
    
    # Clone repo
    subprocess.run(["rm", "-rf", workdir], capture_output=True)
    subprocess.run(["git", "clone", clone_url, workdir], capture_output=True, timeout=120)
    
    # Run KorgKode
    cmd = [
        "python3", f"{KORGKODE_PATH}/korgkode.sh",
        task,
        "--repo", workdir,
    ]
    env = {**os.environ}
    subprocess.run(cmd, capture_output=True, timeout=600, env=env)
    
    print(f"KorgKode completed for {repo}")


def start_webhook_server(host: str = "0.0.0.0", port: int = 8091):
    """Start the KorgKode webhook server."""
    app = create_webhook_app()
    if app is None:
        print("Install FastAPI: pip install fastapi uvicorn")
        return
    
    print(f"🔌 KorgKode Webhook Server: http://{host}:{port}/webhook")
    print(f"   Health check: http://{host}:{port}/health")
    uvicorn.run(app, host=host, port=port, log_level="info")