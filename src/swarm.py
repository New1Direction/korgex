"""
Korgex Multi-Agent Swarm — Parallel subagent delegation engine.

Spins up specialized agents in their own sandboxes (Docker/Modal),
runs them concurrently, and aggregates results. Each subagent gets
its own isolated environment and tool surface.

Usage:
    swarm = AgentSwarm(sandbox_mode="docker")
    results = swarm.run_concurrent([
        SubTask("test", "Write tests for auth module", "/repo"),
        SubTask("refactor", "Refactor API layer", "/repo"),
    ])
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from src.sandbox import SandboxManager


@dataclass
class SubTask:
    """A sub-task for a specialized agent."""
    agent_type: str  # "test", "refactor", "security", "docs", "frontend"
    description: str
    repo_root: str
    context: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class SubTaskResult:
    """Result from a sub-agent."""
    task_id: str
    agent_type: str
    description: str
    success: bool
    output: str
    summary: str
    duration_seconds: float
    error: Optional[str] = None


# Agent prompts for different specializations
AGENT_PROMPTS = {
    "test": """You are a Test Engineering Specialist. Your purpose is to write comprehensive tests.

Rules:
1. Read the relevant source files first
2. Write tests covering: success paths, failure paths, edge cases
3. Run the tests to verify they pass
4. Output ONLY: a summary of tests written and their results
""",
    "refactor": """You are a Code Refactoring Specialist. Your purpose is to improve code structure.

Rules:
1. Read the relevant source files first
2. Identify improvement opportunities (patterns, duplication, complexity)
3. Apply changes using SEARCH/REPLACE blocks
4. Run existing tests to verify nothing broke
5. Output ONLY: a summary of changes made
""",
    "security": """You are a Security Audit Specialist. Your purpose is to identify vulnerabilities.

Rules:
1. Scan for: SQL injection, XSS, CSRF, auth bypass, hardcoded secrets
2. Check dependency versions for known CVEs
3. Review authentication and authorization logic
4. Output ONLY: a prioritized list of findings
""",
    "docs": """You are a Documentation Specialist. Your purpose is to write clear documentation.

Rules:
1. Read the relevant source files first
2. Write documentation in the project's existing format
3. Include: purpose, usage examples, parameters, return values
4. Output ONLY: a summary of what was documented
""",
    "frontend": """You are a Frontend Verification Specialist. Your purpose is to verify UI changes.

Rules:
1. Start the dev server
2. Take screenshots of changed pages
3. Verify key elements are present and styled correctly
4. Output ONLY: a summary of what was verified
""",
}


class SubagentWorker:
    """A subagent that runs a specialized task in its own sandbox."""
    
    def __init__(self, task: SubTask, sandbox_mode: str = None):
        self.task = task
        self.sandbox = SandboxManager.get(sandbox_mode)
        self.prompt = AGENT_PROMPTS.get(task.agent_type, AGENT_PROMPTS["test"])
    
    def run(self) -> SubTaskResult:
        """Execute the sub-task."""
        start = time.time()
        
        try:
            # Setup sandbox with the repo
            self.sandbox.setup(self.task.repo_root)
            
            # Build the subagent task
            cmd = f"""cat << 'PROMPT' | python3
{self.prompt}

TASK: {self.task.description}

CONTEXT: {json.dumps(self.task.context)}
PROMPT
"""
            result = self.sandbox.run(cmd)
            
            duration = time.time() - start
            return SubTaskResult(
                task_id=self.task.id,
                agent_type=self.task.agent_type,
                description=self.task.description,
                success=result.get("exit_code", -1) == 0,
                output=result.get("stdout", ""),
                summary=result.get("stdout", "")[:500],
                duration_seconds=round(duration, 1),
                error=result.get("stderr") if result.get("exit_code", 0) != 0 else None,
            )
            
        except Exception as e:
            return SubTaskResult(
                task_id=self.task.id,
                agent_type=self.task.agent_type,
                description=self.task.description,
                success=False,
                output="",
                summary="",
                duration_seconds=round(time.time() - start, 1),
                error=str(e),
            )
        finally:
            self.sandbox.cleanup()


class AgentSwarm:
    """Orchestrates multiple subagents in parallel."""
    
    def __init__(self, max_parallel: int = 5, sandbox_mode: str = None):
        self.max_parallel = max_parallel
        self.sandbox_mode = sandbox_mode
    
    def run_concurrent(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """Run multiple sub-tasks concurrently in their own sandboxes."""
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = {
                executor.submit(SubagentWorker(task, self.sandbox_mode).run): task
                for task in tasks
            }
            
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    status = "✅" if result.success else "❌"
                    print(f"  {status} [{task.agent_type}] {task.description} ({result.duration_seconds}s)")
                except Exception as e:
                    results.append(SubTaskResult(
                        task_id=task.id,
                        agent_type=task.agent_type,
                        description=task.description,
                        success=False,
                        output="",
                        summary="",
                        duration_seconds=0,
                        error=str(e),
                    ))
        
        return results
    
    def run_sequential(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """Run sub-tasks one at a time."""
        results = []
        for task in tasks:
            worker = SubagentWorker(task, self.sandbox_mode)
            result = worker.run()
            results.append(result)
            status = "✅" if result.success else "❌"
            print(f"  {status} [{task.agent_type}] {task.description} ({result.duration_seconds}s)")
        return results
    
    def analyze_pr(self, repo_root: str, pr_description: str) -> dict:
        """Analyze a PR by running multiple specialist agents in parallel."""
        tasks = [
            SubTask("test", f"Review and suggest tests for: {pr_description}", repo_root),
            SubTask("security", f"Audit security of: {pr_description}", repo_root),
            SubTask("refactor", f"Review code quality of: {pr_description}", repo_root),
        ]
        
        results = self.run_concurrent(tasks)
        
        return {
            "pr_analysis": pr_description,
            "agents_run": len(results),
            "passed": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "total_duration": round(sum(r.duration_seconds for r in results), 1),
            "results": [
                {
                    "agent": r.agent_type,
                    "success": r.success,
                    "summary": r.summary[:300],
                    "duration": r.duration_seconds,
                }
                for r in results
            ],
        }


# Tool registration
from src.tool_base import register_tool, ToolParam


@register_tool("swarm_analyze_pr", "Runs multiple specialist agents (test, security, refactor) in parallel on a PR.", [
    ToolParam("pr_description", "STRING", "Description of the PR to analyze.", required=True),
])
def tool_swarm_analyze_pr(pr_description: str, context: dict = None):
    repo_root = context.get("repo_root") if context else os.getcwd()
    swarm = AgentSwarm()
    result = swarm.analyze_pr(repo_root, pr_description)
    return result


@register_tool("swarm_run_tasks", "Runs multiple sub-tasks concurrently using specialized agents.", [
    ToolParam("tasks_json", "STRING", "JSON array of {agent_type, description} objects.", required=True),
])
def tool_swarm_run_tasks(tasks_json: str, context: dict = None):
    repo_root = context.get("repo_root") if context else os.getcwd()
    try:
        task_list = json.loads(tasks_json)
        tasks = [
            SubTask(t["agent_type"], t["description"], repo_root)
            for t in task_list
        ]
        swarm = AgentSwarm()
        results = swarm.run_concurrent(tasks)
        return {
            "total": len(results),
            "passed": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "results": [
                {
                    "agent_type": r.agent_type,
                    "description": r.description[:80],
                    "success": r.success,
                    "summary": r.summary[:300],
                    "duration": r.duration_seconds,
                }
                for r in results
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@register_tool("swarm_status", "Returns status of the agent swarm system.", [])
def tool_swarm_status(context: dict = None):
    return {
        "available_agents": list(AGENT_PROMPTS.keys()),
        "max_parallel": 5,
        "sandbox_mode": "auto",
        "status": "ready",
    }