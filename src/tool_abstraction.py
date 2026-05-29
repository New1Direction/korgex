"""
Korgex Tool Abstraction Layer — Claude Code-inspired tool architecture.

Maps 49 internal tools to ~12 user-facing tool names with deep descriptions
containing usage patterns, edge cases, and anti-patterns embedded directly
in the schema (not in the system prompt).

Architecture:
    [Model] → user-facing tool name → ToolRouter → internal handler
    
    Each user-facing tool has:
    - Simple, intuitive name (Read, Write, Edit, Bash, Grep, Glob, etc.)
    - 3-10 paragraph description with usage patterns, edge cases, gotchas
    - Minimal parameter schemas (2-5 params each)
"""

import json
import os
from typing import Any, Callable, Dict, Optional

# ── User-Facing Tool Definitions ────────────────────────────────────────
# These are the ~12 tools the LLM sees. Each has deep descriptions.
# Internal implementations route to the 49+ registered handlers.

USER_TOOLS = {}

def register_user_tool(name: str, description: str, params: list[dict],
                       handler_name: str = None, aliases: list[str] = None):
    """Register a user-facing tool that routes to one or more internal handlers."""
    USER_TOOLS[name] = {
        "name": name,
        "description": description,
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {p["name"]: {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
                **({"default": p["default"]} if "default" in p else {}),
                **({"enum": p["enum"]} if "enum" in p else {}),
            } for p in params},
            "required": [p["name"] for p in params if p.get("required", False)],
            "additionalProperties": False,
        },
        "handler_name": handler_name or name.lower(),
        "aliases": aliases or [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS — matching Claude Code's style
# ═══════════════════════════════════════════════════════════════════════════

register_user_tool("Read", """
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file
assume that they want you to read it.

Use this tool for:
- Reading source code files to understand implementation details
- Reading configuration files, logs, or error output
- Checking file contents before editing

For large files, use the offset and limit parameters to read specific sections.
The tool returns content with line numbers for easy reference.
""".strip(), [
    {"name": "file_path", "type": "string", "description": "The absolute path to the file to read", "required": True},
    {"name": "offset", "type": "integer", "description": "The line number to start reading from. Only provide if the file is too large to read at once"},
    {"name": "limit", "type": "integer", "description": "The number of lines to read. Only provide if the file is too large to read at once. Maximum 2000 lines."},
])

register_user_tool("Write", """
Creates a new file or completely overwrites an existing file with new content.
Use this tool for creating new files, or when making large-scale changes to existing files.

For smaller, targeted changes to existing files, prefer the Edit tool instead.
The Edit tool performs precise string replacements and is more efficient for modifications.

Usage:
- Always provide the complete file content — this tool overwrites the entire file
- Creates parent directories automatically if they don't exist
- Supports all text file types (source code, configs, markdown, etc.)
""".strip(), [
    {"name": "file_path", "type": "string", "description": "The absolute path to the file to write", "required": True},
    {"name": "content", "type": "string", "description": "The complete content to write to the file", "required": True},
])

register_user_tool("Edit", """
Performs exact string replacements in files.

Usage:
- You must use your Read tool at least once in the conversation before editing.
  This tool will error if you attempt an edit without reading the file first.
- The old_string must be unique in the file (unless replace_all=true).
  Include enough surrounding context to ensure uniqueness.
- The new_string replaces the old_string. It can be empty to delete text.
- This tool works best with SEARCH/REPLACE blocks that match Claude Code's format.

For creating new files, use Write. For small changes to existing code, Edit is preferred.
""".strip(), [
    {"name": "file_path", "type": "string", "description": "The absolute path to the file to modify", "required": True},
    {"name": "old_string", "type": "string", "description": "The text to replace", "required": True},
    {"name": "new_string", "type": "string", "description": "The text to replace it with (must be different from old_string)", "required": True},
    {"name": "replace_all", "type": "boolean", "description": "Replace all occurrences of old_string (default false)", "default": False},
])

register_user_tool("Bash", """
Executes a given bash command and returns its output.

The working directory persists between commands, but shell state does not.
The shell environment is initialized from the user's profile.

Usage:
- Prefer dedicated tools (Read, Edit, Write, Glob, Grep) over Bash when one fits.
  Reserve Bash for shell-only operations (git, npm, pip, system commands).
- For long-running commands, set an appropriate timeout.
- Use simple, one-shot commands. Chain commands with && or ; when needed.
- When running commands that produce large output, pipe through head or grep.

IMPORTANT: Do not run interactive or pager commands (less, vim, nano, etc.).
""".strip(), [
    {"name": "command", "type": "string", "description": "The command to execute", "required": True},
    {"name": "timeout", "type": "integer", "description": "Optional timeout in milliseconds (max 600000)", "default": 180000},
    {"name": "description", "type": "string", "description": "Clear, concise description of what this command does. For simple commands keep it brief (5-10 words)."},
])

register_user_tool("Grep", """
A powerful search tool built on ripgrep.

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command.
- Supports regex patterns, file glob filtering, and path scoping.

Output modes:
- "content": Shows matching lines with line numbers and context (default)
- "files_with_matches": Only shows file paths that contain matches
- "count": Shows match counts per file

Use this when you need to find function definitions, variable usage, or any text pattern.
""".strip(), [
    {"name": "pattern", "type": "string", "description": "The regular expression pattern to search for", "required": True},
    {"name": "path", "type": "string", "description": "File or directory to search in. Defaults to working directory."},
    {"name": "glob", "type": "string", "description": "Glob pattern to filter files (e.g. '*.py', '*.{ts,tsx}')"},
    {"name": "output_mode", "type": "string", "description": "Output mode: 'content' (default), 'files_with_matches', or 'count'", "default": "content", "enum": ["content", "files_with_matches", "count"]},
])

register_user_tool("Glob", """
Fast file pattern matching tool that works with any codebase size.
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead
""".strip(), [
    {"name": "pattern", "type": "string", "description": "The glob pattern to match files against", "required": True},
    {"name": "path", "type": "string", "description": "The directory to search in. Omit to use the current working directory."},
])

register_user_tool("Agent", """
Launch a new agent to handle complex, multi-step tasks. Each agent type has specific capabilities and tools available to it.

Available agent types and the tools they have access to:
- explore: File reading, globbing, grepping — for broad codebase exploration
- plan: Analysis and planning — for breaking down complex tasks
- code: Full tool access — for implementing features and fixes
- review: Read-only tools — for code review and analysis
- research: Web search and reading — for external research

Use the Agent tool when:
- A task can be parallelized across independent sub-tasks
- You need to protect the main context window from excessive results
- A task matches a specialized agent's description

Do NOT use Agent for simple, single-step operations that you can do directly.
""".strip(), [
    {"name": "description", "type": "string", "description": "A short (3-5 word) description of the task", "required": True},
    {"name": "prompt", "type": "string", "description": "The task for the agent to perform", "required": True},
    {"name": "subagent_type", "type": "string", "description": "The type of specialized agent: explore, plan, code, review, research", "default": "code", "enum": ["explore", "plan", "code", "review", "research"]},
    {"name": "model", "type": "string", "description": "Optional model override: sonnet, opus, haiku", "enum": ["sonnet", "opus", "haiku"]},
    {"name": "run_in_background", "type": "boolean", "description": "Run this agent in the background. You'll be notified when it completes.", "default": False},
])

register_user_tool("AskUserQuestion", """
Use this tool when you need to ask the user questions during execution.
This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation trade-offs

Ask the minimum number of questions needed. Prefer yes/no or multiple choice.
Don't ask questions about things you can figure out yourself.
""".strip(), [
    {"name": "questions", "type": "array", "description": "Questions to ask the user (1-4 questions)", "required": True,
     "items": {
         "type": "object",
         "properties": {
             "question": {"type": "string", "description": "The complete question. Clear, specific, ends with ?"},
             "header": {"type": "string", "description": "Very short label (max 12 chars). Examples: 'Auth method', 'Test scope'"},
             "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple choice options (2-4)"},
             "multi_select": {"type": "boolean", "description": "Allow selecting multiple options", "default": False},
         }
     }},
])

register_user_tool("TaskCreate", """
Plan and track work using tasks. Tasks are tracked in the current conversation only.
Use this to break down complex work into discrete, trackable steps.

Mark each task completed as soon as it's done; don't batch.
""".strip(), [
    {"name": "tasks", "type": "array", "description": "Task descriptions (1-10 items)", "required": True,
     "items": {"type": "string"}},
])

register_user_tool("Skill", """
Invoke an installed skill by name. Skills are reusable workflows that automate specific tasks.
Only use skills that are listed as available — don't guess skill names.

Skills are triggered when the user types /<skill-name> or when you determine
a skill matches the current task.
""".strip(), [
    {"name": "skill", "type": "string", "description": "The name of the skill to invoke", "required": True},
    {"name": "args", "type": "string", "description": "Optional arguments to pass to the skill"},
])

register_user_tool("ToolSearch", """
Search for available tools at runtime. Use this when you're unsure which tool to use
for a specific task. Returns tool descriptions that match your query.

This is useful when:
- You're new to this environment and want to discover capabilities
- You need a tool but aren't sure of its exact name
- You want to find alternatives to a tool that didn't work
""".strip(), [
    {"name": "query", "type": "string", "description": "Search query describing what you want to do", "required": True},
])

register_user_tool("Recall", """
Recall what happened in PAST sessions from the korg ledger. korgex records every
prompt, inference, and tool call to a causal journal — this searches it.

Use this when:
- The user references prior work ("how did I fix X last time?", "what did we decide about Y?")
- You want to reuse a procedure that already worked instead of re-deriving it
- You need context from a session before this one

Results are reconciled against the LIVE workspace: each hit that references a file
is checked for drift (content changed / file gone since it was recorded). TRUST
CURRENT STATE OVER STALE MEMORY — if a result is flagged drift=true, re-Read the
file before acting on the recalled detail.
""".strip(), [
    {"name": "query", "type": "string", "description": "What to recall (natural language or keywords)", "required": True},
    {"name": "top_n", "type": "integer", "description": "Max results to return", "default": 5},
    {"name": "mode", "type": "string", "description": "Ranking mode", "default": "auto", "enum": ["auto", "semantic", "substring"]},
])


def get_user_tool_schemas() -> list[dict]:
    """Return all user-facing tool schemas in Claude Code-compatible format."""
    return [t for t in USER_TOOLS.values()]


def get_tool_names() -> list[str]:
    """Return all user-facing tool names."""
    return list(USER_TOOLS.keys())


# ── Router ──────────────────────────────────────────────────────────────
# Maps model-facing user-facing tools → internal internal handlers.
# Three pieces:
#   handler/module : the real function to call
#   param_map      : rename kwargs (e.g. file_path → filepath)
#   adapter        : when a structural transform is needed (e.g. old/new → SEARCH/REPLACE)
# Anything not in this map returns {"error": "Unknown tool: ..."}.
#
# Additionally, tools registered via register_mcp_tool() route through the
# MCP server manager instead of an in-process handler.

import inspect

# Track which tool names came from MCP servers (vs. native handlers)
_MCP_TOOLS: set[str] = set()


def register_mcp_tool(tool) -> None:
    """Add a tool discovered from an MCP server to the user-facing registry.

    `tool` is an MCPTool dataclass: {name, description, input_schema, server_name}.
    The tool becomes visible to the LLM via USER_TOOLS, and route_tool_call
    dispatches it back to the originating MCP server.
    """
    USER_TOOLS[tool.name] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema or {
            "type": "object", "properties": {}, "required": [],
        },
        "handler_name": tool.name,
        "aliases": [],
        "_mcp_server": tool.server_name,
    }
    _MCP_TOOLS.add(tool.name)


def unregister_mcp_tools() -> int:
    """Remove all MCP-sourced tools (used on shutdown). Returns count removed."""
    n = len(_MCP_TOOLS)
    for name in list(_MCP_TOOLS):
        USER_TOOLS.pop(name, None)
    _MCP_TOOLS.clear()
    return n


def _adapter_edit(params: dict) -> dict:
    """Claude-Code Edit (old_string/new_string) → Jules SEARCH/REPLACE merge_diff."""
    return {
        "filepath": params["file_path"],
        "merge_diff": (
            f"<<<<<<< SEARCH\n{params['old_string']}\n"
            f"=======\n{params['new_string']}\n"
            f">>>>>>> REPLACE"
        ),
    }


_TOOL_ROUTING = {
    "Read":  {"handler": "tool_read_file",                 "module": "src.tools_impl",
              "param_map": {"file_path": "filepath"}},
    "Write": {"handler": "tool_write_file",                "module": "src.tools_impl",
              "param_map": {"file_path": "filepath"}},
    "Edit":  {"handler": "tool_replace_with_git_merge_diff", "module": "src.tools_impl",
              "adapter": _adapter_edit},
    "Bash":  {"handler": "tool_run_in_bash_session",       "module": "src.tools_impl",
              "param_map": {"command": "command"}},
    "Grep":  {"handler": "tool_grep",                      "module": "src.tools_impl",
              "param_map": {"pattern": "pattern"}},
    "Glob":  {"handler": "tool_list_files",                "module": "src.tools_impl",
              "param_map": {"path": "path"}},
    "Recall": {"handler": "tool_recall",                   "module": "src.recall"},
}


def route_tool_call(tool_name: str, params: dict) -> dict:
    """Route a user-facing tool call to the appropriate handler.

    Dispatch order:
      1. If the tool was registered from an MCP server → call that server.
      2. Otherwise → look up in _TOOL_ROUTING and call the native handler,
         dropping kwargs the handler doesn't accept and injecting context.
    """
    if tool_name in _MCP_TOOLS:
        try:
            from src.mcp_client import get_manager
            return get_manager().call_tool(tool_name, params)
        except Exception as e:
            return {"error": f"MCP tool {tool_name} failed: {type(e).__name__}: {e}"}

    route = _TOOL_ROUTING.get(tool_name)
    if not route:
        return {"error": f"Unknown tool: {tool_name}"}

    if "adapter" in route:
        mapped = route["adapter"](params)
    else:
        pmap = route.get("param_map", {})
        mapped = {pmap.get(k, k): v for k, v in params.items()}

    import importlib
    mod = importlib.import_module(route["module"])
    handler = getattr(mod, route["handler"], None)
    if not handler:
        return {"error": f"Handler '{route['handler']}' not found"}

    sig = inspect.signature(handler)
    accepted = set(sig.parameters.keys())
    filtered = {k: v for k, v in mapped.items() if k in accepted}
    if "context" in accepted:
        filtered.setdefault("context", {"repo_root": os.getcwd()})

    try:
        return handler(**filtered)
    except Exception as e:
        return {"error": f"Tool {tool_name} failed: {type(e).__name__}: {e}"}


# Save schemas for the agent to use
def save_schemas_to_file(path: str = None):
    """Export all user-facing tool schemas to a JSON file."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "tool_schemas.json")
    schemas = get_user_tool_schemas()
    with open(path, "w") as f:
        json.dump(schemas, f, indent=2)
    return path