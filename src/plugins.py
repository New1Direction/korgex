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

import importlib.util
import os
from typing import Callable

# The in-process lifecycle points plugins may register on.
# `on_assistant_text` fires once per loop round with the model's narration/answer
# text (the ACP bridge streams it to the editor as it's produced).
VALID_HOOKS = frozenset({"on_user_prompt", "pre_tool", "post_tool", "on_stop",
                         "on_assistant_text"})


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


# ── Loading user plugins from disk ───────────────────────────────────────────
# A plugin is a ``.py`` file that defines ``register(registry)`` and wires its
# hooks there. This is what makes korgex extensible without forking it.

def default_plugin_dirs(repo_root: str | None = None, home: str | None = None) -> list:
    """Where drop-in plugins live: ~/.korgex/plugins (user-global) and
    <repo>/.korgex/plugins (project-local)."""
    home = home if home is not None else os.path.expanduser("~")
    dirs = [os.path.join(home, ".korgex", "plugins")]
    if repo_root:
        dirs.append(os.path.join(repo_root, ".korgex", "plugins"))
    return dirs


def load_plugins(registry: "PluginRegistry", dirs) -> list:
    """Import every ``*.py`` in `dirs` and call its ``register(registry)``.

    Returns ``[{name, ok, error}]`` for each plugin considered. Fail-safe: a plugin
    that won't import, lacks ``register``, or raises while registering is recorded
    with ``ok=False`` and skipped — it never crashes startup, and the others still
    load. Files named ``__*`` or ``_*`` are ignored (helpers, not plugins).
    """
    loaded = []
    for d in dirs or []:
        if not d or not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            name = fname[:-3]
            path = os.path.join(d, fname)
            try:
                spec = importlib.util.spec_from_file_location(f"korgex_plugin_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)          # may raise → caught below
                register = getattr(module, "register", None)
                if not callable(register):
                    loaded.append({"name": name, "ok": False,
                                   "error": "no register(registry) function"})
                    continue
                register(registry)                       # may raise → caught below
                loaded.append({"name": name, "ok": True, "error": None})
            except Exception as e:
                loaded.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return loaded
