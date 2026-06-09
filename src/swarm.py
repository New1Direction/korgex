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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional


def _default_agent_factory(task: "SubTask"):
    """Build a real, non-interactive KorgexAgent for a subtask.

    Lazy import keeps swarm importable without pulling the whole agent stack and
    avoids any import cycle with agent.py.
    """
    from src.agent import KorgexAgent
    return KorgexAgent(repo_root=task.repo_root, interactive=False)


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
    """A subagent that runs a specialized task as a real, isolated agent run.

    Previously this piped the prompt into a bare `python3` (which executed no
    LLM at all). It now constructs a real KorgexAgent and runs the task, so the
    swarm performs actual work and every subagent's events land in the shared
    korg ledger.
    """

    def __init__(self, task: SubTask, sandbox_mode: str = None, agent_factory=None):
        self.task = task
        # Retained for API compatibility; execution no longer gates on a sandbox.
        self.sandbox_mode = sandbox_mode
        self.agent_factory = agent_factory
        self.prompt = AGENT_PROMPTS.get(task.agent_type, AGENT_PROMPTS["test"])

    def run(self) -> SubTaskResult:
        """Execute the sub-task by running a real agent."""
        start = time.time()
        try:
            factory = self.agent_factory or _default_agent_factory
            agent = factory(self.task)
            full_prompt = (
                f"{self.prompt}\n\nTASK: {self.task.description}\n\n"
                f"CONTEXT: {json.dumps(self.task.context)}"
            )
            result = agent.run_task(full_prompt)

            duration = time.time() - start
            success = bool(result.get("success"))
            output = str(result.get("result", ""))
            return SubTaskResult(
                task_id=self.task.id,
                agent_type=self.task.agent_type,
                description=self.task.description,
                success=success,
                output=output,
                summary=output[:500],
                duration_seconds=round(duration, 1),
                error=None if success else output,
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


class AgentSwarm:
    """Orchestrates multiple subagents in parallel."""
    
    def __init__(self, max_parallel: int = 5, sandbox_mode: str = None, agent_factory=None):
        self.max_parallel = max_parallel
        self.sandbox_mode = sandbox_mode
        self.agent_factory = agent_factory

    def run_concurrent(self, tasks: list[SubTask]) -> list[SubTaskResult]:
        """Run multiple sub-tasks concurrently as real agent runs."""
        results = []

        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = {
                executor.submit(SubagentWorker(task, self.sandbox_mode, self.agent_factory).run): task
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
            worker = SubagentWorker(task, self.sandbox_mode, self.agent_factory)
            result = worker.run()
            results.append(result)
            status = "✅" if result.success else "❌"
            print(f"  {status} [{task.agent_type}] {task.description} ({result.duration_seconds}s)")
        return results

    def run_graph(self, nodes) -> dict:
        """Run subtasks as a dependency DAG instead of a flat fan-out.

        `nodes` are ``exec_graph.Node`` objects whose ``.task`` is a SubTask and
        whose ``.deps`` are other node ids. Independent nodes run in parallel (up
        to ``max_parallel``); a subtask that fails blocks its dependents, which are
        reported as skipped rather than run against a broken precondition. Returns
        the exec_graph run report — ``results[node_id]`` is the SubTaskResult.
        """
        from src.exec_graph import ExecGraph

        def _execute(node):
            worker = SubagentWorker(node.task, self.sandbox_mode, self.agent_factory)
            res = worker.run()
            if not res.success:
                # Surface failure to the DAG so dependents skip instead of running
                # against an unmet precondition.
                raise RuntimeError(res.error or "subtask failed")
            return res

        return ExecGraph(nodes).run(_execute, max_parallel=self.max_parallel)

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