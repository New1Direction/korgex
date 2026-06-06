"""Agent-native introspection — emits a `korg:introspect@v1` document
describing every korgex callable, its argument schema, declared
side-effects, output mode, and stable command ID.

This is the Python sibling of:
  - `Korg/adapters/recall-mcp/src/korg_recall_mcp/introspect.py`
  - `Korg/src/introspect.rs`
  - `API/thumper/src/cli/introspect.rs`

All four emit `korg:introspect@v1`. Cross-language agents see one
schema across the entire korg ecosystem.

This module has zero runtime dependencies beyond the Python stdlib so
`korgex --introspect` works in any environment that can run korgex
at all (no pip install needed for the introspect surface).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


INTROSPECT_SCHEMA_ID = "korg:introspect@v1"
BINARY_NAME = "korgex"


@dataclass(frozen=True)
class Capabilities:
    """Declared static behavior of a callable. Conservative defaults —
    zero-effect — so agents don't get surprised by undeclared side-effects."""

    output_mode: str = "envelope"      # "none" | "stream" | "envelope" | "session"
    side_effects: str = "none"         # "none" | "fs_read" | "fs_write" | "network" | "ledger_write"
    requires_project: bool = False
    long_running: bool = False
    stateful: bool = False
    reads_stdin: bool = False
    supports_output_path: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Callable:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    surfaces: list[str] = field(default_factory=lambda: ["cli"])
    capabilities: Capabilities = field(default_factory=Capabilities)

    def to_introspect_entry(self) -> dict[str, Any]:
        return {
            "command_id": self.id,
            "name": self.name,
            "description": self.description,
            "surfaces": list(self.surfaces),
            "input_schema": self.input_schema,
            "capabilities": self.capabilities.to_dict(),
        }


# Canonical exit-code table shared across the korg ecosystem.
# In-Python form is int-keyed; wire format stringifies.
EXIT_CODES: dict[int, str] = {
    0: "success",
    1: "error.generic",
    2: "error.usage",
    3: "error.config",
    4: "error.io",
    5: "error.network",
    6: "error.user_interrupt",
    7: "error.dependency_missing",
}


def get_callables() -> list[Callable]:
    """The complete callable surface korgex exposes today.

    Stable IDs use `korgex.<command>` for top-level and
    `korgex.<group>.<sub>` for nested.
    """
    return [
        Callable(
            id="korgex.agent",
            name="agent",
            description=(
                "Run the autonomous korgex agent on a free-form prompt. "
                "The default behavior when invoked with a positional argument. "
                "Iterates an LLM loop, executes tools, writes files, records "
                "every event into a korg ledger via the in-process bridge."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Free-form task description for the agent.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Explicit model override (e.g. claude-sonnet-4-6, gpt-4o).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["plan", "execute", "explore", "review", "debug", "research"],
                        "description": "Mode-based model selection. --model wins if both set.",
                    },
                    "mcp": {
                        "type": "boolean",
                        "default": False,
                        "description": "Load MCP servers from mcp.json at startup.",
                    },
                    "quiet": {
                        "type": "boolean",
                        "default": False,
                        "description": "Disable the streaming TUI; only the final result text prints.",
                    },
                    "resume": {
                        "type": "boolean",
                        "default": False,
                        "description": "Resume the last session — replay the verifiable journal back into context.",
                    },
                },
                "required": ["prompt"],
            },
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="stream",
                side_effects="ledger_write",  # writes to .korg/journal.json + workspace files
                long_running=True,
                stateful=True,
                requires_project=False,
            ),
        ),
        Callable(
            id="korgex.serve",
            name="serve",
            description=(
                "Start the FastAPI dashboard on localhost:8090 and open VS Code "
                "with the sidecar extension. Long-running."
            ),
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="session",
                side_effects="network",
                long_running=True,
                stateful=True,
            ),
        ),
        Callable(
            id="korgex.dashboard",
            name="dashboard",
            description="Start only the dashboard on localhost:8090 (no editor launch).",
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="session",
                side_effects="network",
                long_running=True,
                stateful=True,
            ),
        ),
        Callable(
            id="korgex.init",
            name="init",
            description=(
                "One-shot setup: pip-install Python deps + npm-install + "
                "compile the VS Code sidecar extension into a .vsix."
            ),
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="stream",
                side_effects="fs_write",
                long_running=True,
            ),
        ),
        Callable(
            id="korgex.status",
            name="status",
            description="Report whether the background backend is running.",
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="envelope",
                side_effects="none",
            ),
        ),
        Callable(
            id="korgex.stop",
            name="stop",
            description="Terminate the background backend (SIGTERM, then SIGKILL on timeout).",
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="envelope",
                side_effects="none",
            ),
        ),
        Callable(
            id="korgex.install-extension",
            name="install-extension",
            description="Install the compiled .vsix into the local VS Code installation.",
            input_schema={"type": "object"},
            surfaces=["cli"],
            capabilities=Capabilities(
                output_mode="stream",
                side_effects="fs_write",
                long_running=True,
            ),
        ),
    ]


def build_document(version: str) -> dict[str, Any]:
    """Build the full `--introspect` document.

    Note: `exit_codes` keys are emitted as strings since JSON has no
    integer keys. Agents reading the wire format use `doc["exit_codes"]["0"]`.
    """
    return {
        "schema": INTROSPECT_SCHEMA_ID,
        "binary": BINARY_NAME,
        "version": version,
        "callables_declared": True,
        "callables": [c.to_introspect_entry() for c in get_callables()],
        "exit_codes": {str(k): v for k, v in EXIT_CODES.items()},
    }


def emit(version: str) -> None:
    """Print the document to stdout. Used by the `--introspect` short-circuit."""
    print(json.dumps(build_document(version), indent=2))
