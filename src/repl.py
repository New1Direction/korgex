"""korgex interactive REPL — the conversational shell that makes korgex feel like
a terminal-native coding agent: stay in a session, talk across turns, swap models
mid-conversation (``/model``), all provider-agnostic.

The input PARSER (`parse_repl_input`) is pure and fully tested. The `Repl` loop is
a thin shell over the parser + the agent's existing streaming path, so the part
that's hard to unit-test (a live readline loop + a network stream) stays minimal.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from src import config as _config

_EXIT = {"/exit", "/quit", "/q"}
_HELP = {"/help", "/?"}
_CLEAR = {"/clear"}

_HELP_TEXT = """\
korgex — commands
  /model [name]   show models, or switch the live model mid-session
  /clear          start a fresh conversation
  /help  /?       this help
  /exit  /quit    leave (also Ctrl-D)
anything else is a message to the agent.
"""


@dataclass
class Command:
    """A parsed REPL line. `kind` ∈ turn|model|help|clear|exit|unknown|noop."""
    kind: str
    arg: str | None = None


def parse_repl_input(line: str) -> Command:
    """Pure: classify one REPL line. Total — every input maps to a Command."""
    s = (line or "").strip()
    if not s:
        return Command("noop")
    # A command is a line that STARTS with "/". Text merely containing a slash
    # mid-sentence (e.g. "what does /etc/hosts do") is a turn.
    if s.startswith("/"):
        head, _, rest = s.partition(" ")
        rest = rest.strip()
        if head in _EXIT:
            return Command("exit")
        if head in _HELP:
            return Command("help")
        if head in _CLEAR:
            return Command("clear")
        if head == "/model":
            return Command("model", rest or None)
        return Command("unknown", head.lstrip("/"))
    return Command("turn", s)


# Suggested models per provider — suggestions, NOT an allowlist (we're not locked
# to any catalog; free-text model ids always work).
SUGGESTED = {
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-4o", "o3", "gpt-4o-mini"],
    "openrouter": ["anthropic/claude-opus-4-8", "openai/gpt-4o", "meta-llama/llama-3.3-70b"],
    "ollama": ["llama3.3", "qwen2.5-coder", "deepseek-r1"],
}


class Repl:
    """Owns the live session: the agent, the running model, the conversation."""

    def __init__(self, cfg: _config.Config | None = None, out=None):
        self.cfg = cfg if cfg is not None else _config.load_config()
        self.out = out or sys.stdout
        self.model, self.api_key = _config.resolve_model_and_key(None, self.cfg)
        self._agent = None  # lazy: built on first turn

    def _print(self, *a):
        print(*a, file=self.out)

    def _models_overview(self) -> str:
        lines = [f"current model: {self.model}"]
        if self.cfg.providers:
            for p in self.cfg.providers:
                t = p.get("type", "?")
                sug = ", ".join(SUGGESTED.get(t, []))
                lines.append(f"  {t}: {sug}" if sug else f"  {t}")
        else:
            lines.append("  (no providers connected — run `korgex setup`)")
        lines.append("switch with: /model <name>")
        return "\n".join(lines)

    def _switch_model(self, name: str):
        self.model, self.api_key = _config.resolve_model_and_key(name, self.cfg)
        if self._agent is not None:
            # Re-point the live agent at the new model (re-resolve its client).
            try:
                self._agent.model = self.model
                self._agent.provider = (
                    "anthropic" if _config.provider_type_for_model(self.model) == "anthropic"
                    else "openai"
                )
                self._agent._client = None  # force re-build on next turn
            except Exception:
                self._agent = None  # safest: rebuild fresh next turn
        self._print(f"→ switched to {self.model}")

    def _ensure_agent(self):
        if self._agent is None:
            from src.agent import KorgexAgent
            self._agent = KorgexAgent(model=self.model, interactive=True)
        return self._agent

    def handle(self, cmd: Command) -> bool:
        """Apply one parsed command. Returns False when the session should end."""
        if cmd.kind == "exit":
            return False
        if cmd.kind == "noop":
            return True
        if cmd.kind == "help":
            self._print(_HELP_TEXT)
            return True
        if cmd.kind == "clear":
            self._agent = None
            self._print("(conversation cleared)")
            return True
        if cmd.kind == "model":
            if cmd.arg is None:
                self._print(self._models_overview())
            else:
                self._switch_model(cmd.arg)
            return True
        if cmd.kind == "unknown":
            self._print(f"unknown command: /{cmd.arg} — try /help")
            return True
        if cmd.kind == "turn":
            self._run_turn(cmd.arg)
            return True
        return True

    def _run_turn(self, text: str):
        """Stream one agent turn. Thin shell over the existing agent loop."""
        agent = self._ensure_agent()
        try:
            agent.run_task(text)
        except KeyboardInterrupt:
            self._print("\n(interrupted)")
        except Exception as e:  # never let one bad turn kill the session
            self._print(f"[error] {e}")

    def run(self):
        """The readline loop. Lands here from a bare `korgex` on a TTY."""
        if not self.cfg.is_configured() and not self.api_key:
            self._print("welcome to korgex — no model connected yet.")
            self._print("run `korgex setup` to connect a provider, then come back.\n")
        self._print(f"korgex · {self.model} · /help for commands, /exit to leave\n")
        while True:
            try:
                line = input("› ")
            except (EOFError, KeyboardInterrupt):
                self._print("")
                break
            if not self.handle(parse_repl_input(line)):
                break
