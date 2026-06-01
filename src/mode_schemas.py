"""
Korgex Mode-Gated Tool Schemas — a plan/execute split.

Each mode exposes a different subset of the 12 user-facing tools.
The model only sees the tools relevant to its current mode, reducing
decision surface and preventing inappropriate actions.

Modes:
  plan     → Read-only + planning (no Write/Edit/Bash)
  execute  → Full tool surface
  explore  → Read-only search (Read, Glob, Grep, Agent(explore))
  review   → Read-only code review (Read, Glob, Grep)
  debug    → Read + Bash + Grep (minimal for debugging)
  research → Web search + reading (no file modifications)

Architecture:
  [Agent Loop] → determines mode → selects tool subset → sends to LLM
"""

from typing import Optional


# ── Tool Subset Definitions ─────────────────────────────────────────────
# Each mode defines which of the 12 user-facing tools are visible.

MODE_TOOL_SETS: dict[str, list[str]] = {
    "plan": [
        "Read",
        "Glob",
        "Grep",
        "Agent",
        "AskUserQuestion",
        "TaskCreate",
        "ToolSearch",
        # Intentionally excluded: Write, Edit, Bash, Skill
    ],
    "execute": [
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Glob",
        "Grep",
        "Agent",
        "AskUserQuestion",
        "TaskCreate",
        "Skill",
        "ToolSearch",
        # Full tool surface — all 11 tools available
    ],
    "explore": [
        "Read",
        "Glob",
        "Grep",
        "Agent",
        "ToolSearch",
        # Read-only exploration — no modifications
        # Intentionally excluded: Write, Edit, Bash, AskUserQuestion
    ],
    "review": [
        "Read",
        "Glob",
        "Grep",
        # Strictly read-only code review
        # No modifications, no agents, no questions
    ],
    "debug": [
        "Read",
        "Bash",
        "Grep",
        "Glob",
        "AskUserQuestion",
        # Read + execute for minimal debugging surface
    ],
    "research": [
        "Read",
        "Glob",
        "Grep",
        "Agent",
        "ToolSearch",
        # Web-enhanced reading
    ],
}


# ── Mode Descriptions ───────────────────────────────────────────────────

MODE_DESCRIPTIONS: dict[str, str] = {
    "plan": """
# Plan Mode
You are in plan mode. Your goal is to analyze and plan — not to execute.

Available: Read files, search code, spawn research agents, ask clarifying questions.
NOT available: Writing files, editing code, running bash commands.

Write your plan to the plan file, then exit plan mode when ready for approval.
""".strip(),

    "execute": """
# Execute Mode
You are in execute mode. Your goal is to implement, test, and verify.

Full tool surface available including file modifications and bash execution.
Prefer dedicated tools (Read, Edit, Write) over Bash when one fits.
""".strip(),

    "explore": """
# Explore Mode
You are in explore mode. Your goal is to understand the codebase.

Read files, search patterns, and spawn exploration agents.
You cannot modify files in this mode.
""".strip(),

    "review": """
# Review Mode
You are reviewing code. Read-only. No modifications.

Read files, search for patterns, and provide analysis.
""".strip(),

    "debug": """
# Debug Mode
You are debugging. Read files, execute bash commands, and grep for patterns.

Focused tool set for root cause analysis.
""".strip(),

    "research": """
# Research Mode
You are researching. Read files, search, and delegate to research agents.

Gathering information from internal and external sources.
""".strip(),
}


# ── Agent Type → Mode Mapping ───────────────────────────────────────────
# Maps the Agent tool's subagent_type to the appropriate mode.

AGENT_TYPE_TO_MODE: dict[str, str] = {
    "explore": "explore",
    "plan": "plan",
    "code": "execute",
    "review": "review",
    "research": "research",
    "debug": "debug",
}


# ── Mode Selection ──────────────────────────────────────────────────────

def get_mode_for_agent(subagent_type: str) -> str:
    """Determine the mode for a given agent type."""
    return AGENT_TYPE_TO_MODE.get(subagent_type, "execute")


def get_mode_description(mode: str) -> str:
    """Get the system prompt section for a given mode."""
    return MODE_DESCRIPTIONS.get(mode, MODE_DESCRIPTIONS["execute"])


def get_tools_for_mode(mode: str) -> list[str]:
    """Get the list of visible tool names for a given mode."""
    return MODE_TOOL_SETS.get(mode, MODE_TOOL_SETS["execute"])


def filter_tool_schemas(mode: str, all_tools: list[dict]) -> list[dict]:
    """Filter tool schemas to only include tools available in this mode.
    
    Args:
        mode: The active mode (plan, execute, explore, etc.)
        all_tools: Full list of user-facing tool schemas
    
    Returns:
        Filtered list containing only tools visible in this mode
    """
    allowed = set(get_tools_for_mode(mode))
    return [t for t in all_tools if t.get("name") in allowed]


# ── Mode State Machine ──────────────────────────────────────────────────

class ModeStateMachine:
    """Tracks the current mode and handles transitions.
    
    A standard plan-mode enter/exit pattern.
    """
    
    def __init__(self, initial_mode: str = "execute"):
        self.current_mode = initial_mode
        self._mode_stack: list[str] = []
    
    def enter_mode(self, mode: str) -> dict:
        """Transition to a new mode, pushing the current one onto the stack."""
        previous = self.current_mode
        self._mode_stack.append(previous)
        self.current_mode = mode
        return {"previous_mode": previous, "current_mode": mode}
    
    def exit_mode(self) -> dict:
        """Return to the previous mode (pop from stack)."""
        if not self._mode_stack:
            return {"error": "No previous mode to return to"}
        previous = self.current_mode
        self.current_mode = self._mode_stack.pop()
        return {"previous_mode": previous, "current_mode": self.current_mode}
    
    def is_plan_mode(self) -> bool:
        """Check if currently in plan mode."""
        return self.current_mode == "plan"
    
    def can_modify_files(self) -> bool:
        """Check if the current mode allows file modifications."""
        return self.current_mode in ("execute", "debug")
    
    def can_execute_bash(self) -> bool:
        """Check if the current mode allows bash execution."""
        return self.current_mode in ("execute", "debug")
    
    def get_mode_prompt_section(self) -> dict:
        """Get the mode-specific system prompt section."""
        return {
            "type": "text",
            "text": get_mode_description(self.current_mode),
        }


# ── Agent Loop Integration ──────────────────────────────────────────────

def build_tool_payload(mode: str, all_tool_schemas: list[dict]) -> list[dict]:
    """Build the tool payload for an API request based on the active mode.
    
    This is the function that plugs into the agent loop to serve
    mode-appropriate tool schemas to the model.
    
    Args:
        mode: Current mode name
        all_tool_schemas: Full set of tool schemas from tool_abstraction.py
    
    Returns:
        Filtered tool schemas + mode description for the system prompt
    """
    return filter_tool_schemas(mode, all_tool_schemas)


# ── Self-Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Mode Tool Subsets ===\n")
    for mode in ["plan", "execute", "explore", "review", "debug", "research"]:
        tools = get_tools_for_mode(mode)
        print(f"{mode:10s} → {len(tools):2d} tools: {', '.join(tools)}")
    
    print("\n=== Mode State Machine ===\n")
    machine = ModeStateMachine("execute")
    print(f"Initial: {machine.current_mode} (can modify: {machine.can_modify_files()})")
    
    machine.enter_mode("plan")
    print(f"After enter plan: {machine.current_mode} (can modify: {machine.can_modify_files()})")
    
    machine.exit_mode()
    print(f"After exit: {machine.current_mode} (can modify: {machine.can_modify_files()})")