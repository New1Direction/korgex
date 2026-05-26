"""
Korgex Tool System — mirrors Jules' 33 extracted tools.

Each tool is a function registered with a name, description, parameters schema,
and a handler. The dispatch system routes tool calls to the right handler.

Tool types match Jules: STRING, BOOLEAN, ARRAY, OBJECT (not JSON Schema standard).
"""

import json
import os
import shlex
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

TOOL_REGISTRY = {}


class ToolParam:
    """A parameter definition matching Jules' format."""
    def __init__(self, name: str, type_name: str, description: str = "", required: bool = False):
        self.name = name
        self.type = type_name.upper()  # STRING, BOOLEAN, ARRAY, OBJECT
        self.description = description
        self.required = required

    def to_dict(self):
        d = {"type": self.type, "description": self.description}
        return d


class ToolDef:
    """A tool definition mirroring Jules' tool schema format."""
    def __init__(self, name: str, description: str, parameters: list[ToolParam] = None):
        self.name = name
        self.description = description
        self.parameters = parameters or []
        self.handler: Optional[Callable] = None

    def to_schema(self) -> dict:
        """Output in Jules' format: {name, description, parameters: {properties, required, type}}"""
        props = {}
        required = []
        for p in self.parameters:
            props[p.name] = p.to_dict()
            if p.required:
                required.append(p.name)

        schema = {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": props,
            }
        }
        if required:
            schema["parameters"]["required"] = required
        return schema

    def parse_args(self, raw_args: dict) -> dict:
        """Parse and validate incoming arguments."""
        parsed = {}
        for p in self.parameters:
            val = raw_args.get(p.name)
            if val is None and p.required:
                raise ValueError(f"Missing required parameter: {p.name}")
            if val is not None:
                if p.type == "STRING":
                    parsed[p.name] = str(val)
                elif p.type == "BOOLEAN":
                    parsed[p.name] = bool(val)
                elif p.type == "ARRAY":
                    parsed[p.name] = list(val) if isinstance(val, (list, tuple)) else [val]
                else:
                    parsed[p.name] = val
        return parsed


def register_tool(name: str, description: str, parameters: list[ToolParam] = None):
    """Decorator to register a tool handler."""
    def decorator(func):
        tool = ToolDef(name, description, parameters)
        tool.handler = func
        TOOL_REGISTRY[name] = tool
        return func
    return decorator


def get_tool_schemas() -> list[dict]:
    """Return all tool schemas in Jules' array format."""
    return [[tool.to_schema()] for tool in TOOL_REGISTRY.values()]


def dispatch_tool(tool_name: str, raw_args: dict, context: dict = None) -> Any:
    """Dispatch a tool call to its handler with parsed args."""
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        parsed = tool.parse_args(raw_args or {})
        result = tool.handler(**parsed, context=context)
        return result
    except Exception as e:
        return {"error": str(e)}


def get_context() -> dict:
    """Create execution context."""
    return {
        "cwd": os.getcwd(),
        "repo_root": _find_repo_root(),
        "sandbox_dir": None,
    }


@lru_cache(maxsize=1)
def _find_repo_root() -> Optional[str]:
    """Find the git repo root from cwd. Cached per process (cwd is stable
    once the agent starts; cache invalidated only on process restart)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return os.getcwd()