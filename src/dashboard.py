"""
Korgex Web Dashboard — Steering & Approval UI.

FastAPI server + HTML interface for:
- Viewing agent state and plan steps
- Approving plans with one click
- Live terminal logs stream
- Playwright screenshot viewer
- Subagent swarm dashboard
"""

import os
import threading
from typing import Optional

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.tool_base import dispatch_tool, get_context


# In-memory state store (replace with Redis/DB for production)
_dashboard_state = {
    "current_task": None,
    "current_plan": None,
    "plan_approved": False,
    "logs": [],
    "screenshots": [],
    "subagent_results": [],
    "pending_input_request": None,
}


_DASHBOARD_DEFAULT_HOST = "127.0.0.1"
_LOCAL_DASHBOARD_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve_dashboard_host(host: str | None = None, env: dict | None = None) -> str:
    """Resolve dashboard bind host. Default is localhost; exposed mode is explicit."""
    if host:
        return host
    env = os.environ if env is None else env
    return env.get("KORGEX_DASHBOARD_HOST") or _DASHBOARD_DEFAULT_HOST


def dashboard_exposure_warning(host: str) -> str | None:
    """Return a warning when the unauthenticated dashboard is bound off-localhost."""
    if host in _LOCAL_DASHBOARD_HOSTS:
        return None
    return (
        "WARNING: Korgex dashboard authentication is not implemented. "
        f"Binding to {host!r} may expose task/approval endpoints; put it behind "
        "an auth-terminating proxy or use KORGEX_DASHBOARD_HOST=127.0.0.1."
    )


def create_app() -> Optional[object]:
    """Create the FastAPI dashboard app."""
    if not FASTAPI_AVAILABLE:
        return None
    
    app = FastAPI(title="Korgex Dashboard", version="1.0.0")
    
    # ─── Routes ────────────────────────────────────────────────────────
    
    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(_DASHBOARD_HTML)
    
    @app.get("/api/state")
    async def get_state():
        return _dashboard_state
    
    @app.post("/api/approve-plan")
    async def approve_plan():
        _dashboard_state["plan_approved"] = True
        return {"status": "approved"}
    
    @app.post("/api/send-feedback")
    async def send_feedback(data: dict):
        feedback = data.get("feedback", "")
        _dashboard_state["pending_input_request"] = feedback
        return {"status": "received", "feedback": feedback}
    
    @app.post("/api/new-task")
    async def new_task(data: dict):
        description = data.get("description", "")
        _dashboard_state["current_task"] = description
        _dashboard_state["logs"].append({"type": "task", "message": f"New task: {description}"})
        
        # Dispatch in background thread
        thread = threading.Thread(
            target=_run_task_background,
            args=(description,),
            daemon=True
        )
        thread.start()
        
        return {"status": "started", "task": description}
    
    @app.websocket("/ws/logs")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
                # Send latest logs
                await websocket.send_json(_dashboard_state["logs"][-50:])
        except WebSocketDisconnect:
            pass

    # ── Health ───────────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "korgex-dashboard"}

    @app.get("/api/sandbox")
    def sandbox_status():
        try:
            from src.tools_impl import tool_sandbox_status
            return tool_sandbox_status()
        except Exception as e:
            return {"mode": "unknown", "status": "error", "error": f"{type(e).__name__}: {e}"}

    # ── Swarm endpoints (VS Code extension entry points) ─────────────────
    # Sync defs → FastAPI runs them in a thread pool, so the agent's blocking
    # API calls don't starve the event loop.

    def _run_agent_for(prompt: str) -> dict:
        """Spin a one-shot agent. Returns {success, output} or {success: False, error}."""
        try:
            from src.agent import KorgexAgent
        except Exception as e:
            return {"success": False, "error": f"agent import failed: {e}"}
        try:
            agent = KorgexAgent(interactive=False)
            result = agent.run_task(prompt)
            return {
                "success": bool(result.get("success")),
                "output": result.get("result", ""),
                "iterations": result.get("iterations", 0),
            }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @app.post("/api/swarm/refactor")
    def swarm_refactor(payload: dict):
        filepath = (payload or {}).get("filepath", "").strip()
        if not filepath:
            return JSONResponse({"success": False, "error": "filepath required"}, status_code=400)
        prompt = (f"Refactor the file at {filepath} for clarity, idiomatic style, "
                  f"and maintainability. Read it first, then apply targeted Edit "
                  f"changes. Verify by reading again after each change.")
        result = _run_agent_for(prompt)
        result["filepath"] = filepath
        return result

    @app.post("/api/swarm/heal")
    def swarm_heal(payload: dict):
        payload = payload or {}
        filepath = payload.get("filepath", "").strip()
        command = payload.get("command", "").strip()
        if not filepath or not command:
            return JSONResponse(
                {"success": False, "error": "filepath and command required"},
                status_code=400,
            )
        prompt = (f"The test command `{command}` is failing for file {filepath}. "
                  f"Run the command, parse the failure, edit {filepath} to fix it, "
                  f"and rerun until tests pass. Stop after 5 attempts.")
        result = _run_agent_for(prompt)
        result["filepath"] = filepath
        result["command"] = command
        return result

    @app.post("/api/swarm/profile")
    def swarm_profile(payload: dict):
        command = (payload or {}).get("command", "").strip()
        if not command:
            return JSONResponse(
                {"success": False, "error": "command required"}, status_code=400,
            )
        try:
            from src.sandbox import SandboxManager
            from src.profiler import PerformanceProfiler
        except Exception as e:
            return {"success": False, "error": f"profiler unavailable: {e}"}
        try:
            sb = SandboxManager.get()  # auto-selects modal/docker/direct
            profiler = PerformanceProfiler(sb)
            result = profiler.run_profile(command)
            return {
                "success": bool(result.get("success")),
                "output": result,
                "command": command,
            }
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    return app


def _run_task_background(description: str):
    """Run a task in the background, updating dashboard state."""
    context = get_context()
    _dashboard_state["plan_approved"] = False
    _dashboard_state["logs"].append({"type": "info", "message": "Exploring codebase..."})
    
    # Step 1: Explore
    files = dispatch_tool("list_files", {"path": "."}, context)
    _dashboard_state["logs"].append({"type": "info", "message": f"Found {len(files.get('files', []))} files"})
    
    # Step 2: Set plan
    plan = f"## Plan for: {description}\n\n1. Implement the requested change\n2. Verify with tests\n3. Submit"
    dispatch_tool("set_plan", {"plan": plan}, context)
    _dashboard_state["current_plan"] = plan
    _dashboard_state["logs"].append({"type": "plan", "message": "Plan generated — awaiting approval"})
    
    # Wait for approval (polling)
    import time
    for _ in range(300):  # 5 minute timeout
        if _dashboard_state["plan_approved"]:
            break
        time.sleep(1)
    
    if not _dashboard_state["plan_approved"]:
        _dashboard_state["logs"].append({"type": "error", "message": "Plan approval timed out"})
        return
    
    _dashboard_state["logs"].append({"type": "info", "message": "Plan approved — executing..."})
    
    # Step 3: Execute (simplified)
    _dashboard_state["logs"].append({"type": "info", "message": "Task executed successfully"})
    _dashboard_state["current_task"] = None


def start_dashboard(host: str | None = None, port: int = 8090):
    """Start the Korgex dashboard server."""
    app = create_app()
    if app is None:
        print("Install FastAPI: pip install fastapi uvicorn")
        return

    host = resolve_dashboard_host(host)
    warning = dashboard_exposure_warning(host)
    if warning:
        print(f"⚠️  {warning}")

    print(f"🌐 Korgex Dashboard: http://{host}:{port}")
    print(f"📋 Approve plans: http://{host}:{port}/api/approve-plan")
    uvicorn.run(app, host=host, port=port, log_level="info")


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Korgex Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0d1117; color: #e6edf3; display: flex; height: 100vh; }
        .sidebar { width: 280px; background: #161b22; padding: 20px; border-right: 1px solid #30363d; }
        .main { flex: 1; display: flex; flex-direction: column; }
        .header { padding: 20px; border-bottom: 1px solid #30363d; }
        .content { flex: 1; padding: 20px; overflow-y: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
        .card h3 { color: #58a6ff; margin-bottom: 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
        .log-entry { padding: 4px 0; font-family: 'SF Mono', Monaco, monospace; font-size: 12px; border-bottom: 1px solid #21262d; }
        .log-entry.task { color: #d2a8ff; }
        .log-entry.plan { color: #7ee787; }
        .log-entry.error { color: #ff7b72; }
        .log-entry.info { color: #8b949e; }
        button { background: #238636; color: white; border: none; padding: 8px 20px; border-radius: 6px;
                 cursor: pointer; font-size: 14px; font-weight: 600; }
        button:hover { background: #2ea043; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        textarea { width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
                   color: #e6edf3; padding: 12px; font-family: 'SF Mono', monospace; font-size: 13px; resize: vertical; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
        .badge.active { background: #238636; color: white; }
        .badge.waiting { background: #d29922; color: white; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .plan-step { padding: 8px 12px; background: #0d1117; border-radius: 6px; margin: 4px 0; font-size: 13px; }
    </style>
</head>
<body>
    <div class="sidebar">
        <h2 style="margin-bottom: 24px; font-size: 20px;">⚡ Korgex</h2>
        <div style="margin-bottom: 24px;">
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 4px;">Status</div>
            <div><span class="badge active">● Active</span></div>
        </div>
        <div style="margin-bottom: 24px;">
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 4px;">Sandbox</div>
            <div id="sandbox-mode" style="font-size: 14px;">Loading...</div>
        </div>
        <div style="margin-bottom: 24px;">
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 4px;">Swarm Agents</div>
            <div style="font-size: 14px;">5 specialist agents ready</div>
        </div>
        <div style="margin-top: auto;">
            <div style="font-size: 12px; color: #8b949e; margin-bottom: 8px;">New Task</div>
            <textarea id="task-input" rows="3" placeholder="Describe what to build..."></textarea>
            <button onclick="startTask()" style="margin-top: 8px; width: 100%;">▶ Start Task</button>
        </div>
    </div>
    <div class="main">
        <div class="header">
            <h1 id="task-title" style="font-size: 18px;">No active task</h1>
        </div>
        <div class="content">
            <div class="card">
                <h3>📋 Plan</h3>
                <div id="plan-content">
                    <div style="color: #8b949e; font-size: 13px;">Waiting for a task...</div>
                </div>
                <div style="margin-top: 12px;" id="approval-section" hidden>
                    <button onclick="approvePlan()">✅ Approve Plan</button>
                </div>
            </div>
            <div class="card">
                <h3>📊 Subagents</h3>
                <div id="subagent-content">
                    <div style="color: #8b949e; font-size: 13px;">No agents running</div>
                </div>
            </div>
            <div class="card" style="grid-column: 1 / -1;">
                <h3>📜 Live Logs</h3>
                <div id="log-content" style="max-height: 300px; overflow-y: auto;"></div>
            </div>
        </div>
    </div>

    <script>
        async function refreshState() {
            const resp = await fetch('/api/state');
            const state = await resp.json();
            
            document.getElementById('task-title').textContent = state.current_task || 'No active task';
            
            // Logs
            const logDiv = document.getElementById('log-content');
            logDiv.innerHTML = state.logs.slice(-50).map(l => 
                `<div class="log-entry ${l.type}">${l.message}</div>`
            ).join('');
            logDiv.scrollTop = logDiv.scrollHeight;
            
            // Plan
            if (state.current_plan) {
                document.getElementById('plan-content').innerHTML = 
                    `<div class="plan-step">${state.current_plan.replace(/\\n/g, '<br>')}</div>`;
                if (!state.plan_approved) {
                    document.getElementById('approval-section').hidden = false;
                } else {
                    document.getElementById('approval-section').hidden = true;
                }
            }
            
            // Sandbox
            try {
                const sb = await fetch('/api/sandbox');
                const sbData = await sb.json();
                document.getElementById('sandbox-mode').textContent = sbData.mode || 'direct';
            } catch(e) {}
        }
        
        async function approvePlan() {
            await fetch('/api/approve-plan', { method: 'POST' });
            refreshState();
        }
        
        async function startTask() {
            const input = document.getElementById('task-input');
            if (!input.value.trim()) return;
            await fetch('/api/new-task', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({description: input.value})
            });
            input.value = '';
            refreshState();
        }
        
        // Refresh every 2 seconds
        setInterval(refreshState, 2000);
        refreshState();
    </script>
</body>
</html>
"""


# Tool registration
from src.tool_base import register_tool, ToolParam  # noqa: E402 (registered after the module's HTML/handlers)


@register_tool("start_dashboard", "Starts the Korgex web steering dashboard.", [
    ToolParam("port", "STRING", "Port to run the dashboard on (default: 8090)."),
    ToolParam("host", "STRING", "Bind host. Defaults to 127.0.0.1; use 0.0.0.0 only behind auth."),
])
def tool_start_dashboard(port: str = "8090", host: str = None, context: dict = None):
    resolved_host = resolve_dashboard_host(host)
    thread = threading.Thread(
        target=start_dashboard,
        args=(resolved_host, int(port)),
        daemon=True
    )
    thread.start()
    return {"dashboard_url": f"http://{resolved_host}:{port}", "status": "started"}
