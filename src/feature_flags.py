"""
Korgex Feature Flags & Beta Headers — inspired by the anthropic-beta header system.

Each feature flag controls a specific capability. Flags are serialized into
beta headers sent with API requests, just like the anthropic-beta header.

Flags also control runtime behavior: tool availability, model settings, etc.
"""

from dataclasses import dataclass


@dataclass
class FeatureFlag:
    """A single feature flag with name, beta header, and description."""
    name: str
    header: str  # The anthropic-beta value (e.g. "context-1m-2025-08-07")
    description: str
    enabled: bool = True
    requires_auth: bool = False


# ── Feature Flag Registry ──────────────────────────────────────────────
# Maps directly to the anthropic-beta header architecture

FEATURES: dict[str, FeatureFlag] = {}

def register_flag(name: str, header: str, description: str,
                  enabled: bool = True, requires_auth: bool = False) -> FeatureFlag:
    """Register a feature flag."""
    flag = FeatureFlag(name=name, header=header, description=description,
                       enabled=enabled, requires_auth=requires_auth)
    FEATURES[name] = flag
    return flag


# beta flags
register_flag(
    "agent_core", "korgex-2025-01-01",
    "Core Korgex agent identity",
    enabled=True,
)
register_flag(
    "context_1m", "context-1m-2025-08-07",
    "Extended 1M context window support",
    enabled=True,
)
register_flag(
    "tool_abstraction", "tool-abstraction-2025-06-01",
    "Tool naming abstraction layer (12 user-facing tools)",
    enabled=True,
)
register_flag(
    "memory_system", "memory-system-2025-07-01",
    "File-based persistent memory with 4 types",
    enabled=True,
)
register_flag(
    "hook_pipeline", "hook-pipeline-2025-03-01",
    "PreTool/PostTool/SessionStart hooks",
    enabled=False,  # Requires user configuration
)
register_flag(
    "session_persistence", "session-persistence-2025-04-01",
    "Save/reload session environments for context resumption",
    enabled=True,
)
register_flag(
    "auto_compact", "auto-compact-2025-05-01",
    "Automatic context window compaction",
    enabled=True,
)
register_flag(
    "agent_subagents", "agent-subagents-2025-02-01",
    "Multi-agent parallel task execution",
    enabled=True,
)
register_flag(
    "effort_control", "effort-2025-11-24",
    "Per-request effort level (speed vs quality)",
    enabled=True,
)
register_flag(
    "cache_diagnosis", "cache-diagnosis-2026-04-07",
    "Cache hit/miss telemetry",
    enabled=True,
)


def get_beta_header() -> str:
    """Generate the anthropic-beta-style header from enabled flags."""
    headers = [f.header for f in FEATURES.values() if f.enabled]
    return ",".join(headers)


def get_beta_headers_dict() -> dict[str, str]:
    """Return all beta headers as a dict for HTTP requests."""
    return {
        "anthropic-beta": get_beta_header(),
        "anthropic-version": "2023-06-01",
        "x-app": "korgex",
    }


def is_enabled(name: str) -> bool:
    """Check if a feature flag is enabled."""
    flag = FEATURES.get(name)
    if not flag:
        return False
    return flag.enabled


def set_enabled(name: str, enabled: bool):
    """Enable or disable a feature flag at runtime."""
    flag = FEATURES.get(name)
    if flag:
        flag.enabled = enabled


def list_features() -> list[dict]:
    """List all registered features with their status."""
    return [
        {"name": f.name, "header": f.header, "enabled": f.enabled,
         "description": f.description}
        for f in FEATURES.values()
    ]


# ── Feature-Gated Imports ──────────────────────────────────────────────

def get_enabled_tools() -> list[str]:
    """Return list of enabled tool names based on feature flags."""
    tools = ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "AskUserQuestion", "TaskCreate"]
    
    if is_enabled("agent_subagents"):
        tools.append("Agent")
    
    if is_enabled("memory_system"):
        tools.extend(["memory_save", "memory_delete", "memory_search", "memory_list"])
    
    if is_enabled("tool_abstraction"):
        tools.extend(["ToolSearch", "Skill"])
    
    return tools