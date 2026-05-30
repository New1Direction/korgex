"""In-process plugin registry for korgex's agent lifecycle.

Complements the shell command-hooks in ``src/hooks.py`` with a low-latency,
in-process surface: register Python callables on lifecycle events and they run
inside the loop with direct access to the call/result. This generalizes the
``witness`` tap into a registerable observer pattern.

    reg = PluginRegistry()
    @reg.on("post_tool")
    def audit(call_and_result): ...

    reg.invoke("post_tool", payload)   # runs every registered observer

Fail-safe by construction: a plugin that raises is isolated — it can never break
the agent loop. An empty registry is a true no-op (zero overhead).
"""
from __future__ import annotations

from typing import Callable

# The in-process lifecycle points plugins may register on.
VALID_HOOKS = frozenset({"on_user_prompt", "pre_tool", "post_tool", "on_stop"})


class PluginRegistry:
    """Holds ordered observers per hook and invokes them defensively."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = {h: [] for h in VALID_HOOKS}

    def register(self, hook: str, fn: Callable) -> Callable:
        if hook not in VALID_HOOKS:
            raise ValueError(f"unknown hook {hook!r}; valid: {sorted(VALID_HOOKS)}")
        self._hooks[hook].append(fn)
        return fn

    def on(self, hook: str) -> Callable:
        """Decorator form: ``@reg.on('pre_tool')``."""
        def deco(fn: Callable) -> Callable:
            return self.register(hook, fn)
        return deco

    def invoke(self, hook: str, *args, **kwargs) -> list:
        """Run every observer for `hook` in registration order; return the list of
        non-None results. A plugin that raises is skipped, never propagated."""
        results = []
        for fn in self._hooks.get(hook, ()):
            try:
                r = fn(*args, **kwargs)
            except Exception:
                continue  # one bad plugin must never break the agent loop
            if r is not None:
                results.append(r)
        return results

    def count(self, hook: str | None = None) -> int:
        if hook is not None:
            return len(self._hooks.get(hook, ()))
        return sum(len(v) for v in self._hooks.values())
