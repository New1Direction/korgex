"""
Korgex — Core Agent Loop (provider-agnostic).

Pipeline:
    user prompt
      → LLM (tools = Claude-Code-style schemas from USER_TOOLS)
      → tool_use blocks
      → route_tool_call → internal handlers (Jules-style tool_* in tools_impl)
      → tool_result back to LLM
      → loop until LLM stops calling tools, or max_iterations
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from src.tool_abstraction import USER_TOOLS, route_tool_call
# tools_impl must be imported so its @register_tool decorators populate the registry
import src.tools_impl  # noqa: F401
from src.korg_ledger import get_default_client as _korg
from src import edit_policy as _EP
from src.plugins import PluginRegistry
from src.hooks import load_hooks, run_event
from src.workspace import path_within
from src.guardrails import is_protected


SYSTEM_PROMPT = """You are Korgex, an extremely skilled software engineer. Your purpose is to assist users by completing coding tasks: solving bugs, implementing features, writing tests, and refactoring code.

CORE DIRECTIVES:
1. PLAN FIRST: Explore the codebase before acting. Read README.md and any AGENTS.md. Understand context before proposing changes.
2. VERIFY WORK: After every modification, read the file back to confirm the change applied correctly.
3. EDIT SOURCE, NOT ARTIFACTS: Never modify build artifacts (dist/, build/, node_modules/, __pycache__/, .next/). Trace back to the source file.
4. PROACTIVE TESTING: Locate relevant tests, run them, and include testing in your plan.
5. DIAGNOSE BEFORE CHANGING: Read error logs and configs before installing new packages or making structural changes.
6. SOLVE AUTONOMOUSLY: Ask the user only when the task is genuinely ambiguous, you are stuck after several attempts, or the scope appears to be changing.

TOOL USE:
- Prefer Read/Edit/Write/Grep/Glob over shelling out to cat/sed/grep/find.
- Use Edit for surgical changes to existing files; use Write only to create new files or for full rewrites.
- When in doubt, Read first. Always Read a file before Editing it.
"""


def _looks_anthropic(model_id: str) -> bool:
    """True for any Claude model — direct Anthropic, OpenRouter (anthropic/claude-...), etc."""
    m = (model_id or "").lower()
    return "claude" in m or m.startswith("anthropic/")


# Read-only tool subset handed to non-mutating subagents (Recall is read-only).
_READONLY_SUBAGENT_TOOLS = ["Read", "Grep", "Glob", "Recall"]

# Map the Agent tool's model alias → a concrete model id.
_MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def subagent_tools(subagent_type: str) -> list:
    """Tool name subset a subagent of `subagent_type` is allowed to use.

    Read-only types (explore/plan/review/research) get search/read tools only.
    The default ("code") gets every tool EXCEPT Agent — subagents must not
    recursively spawn subagents (nesting is one level, like Claude Code's Task).
    """
    if subagent_type in ("explore", "plan", "review", "research"):
        return list(_READONLY_SUBAGENT_TOOLS)
    return [name for name in USER_TOOLS.keys() if name != "Agent"]


def _resolve_params(mode: str) -> dict:
    """Per-mode generation params (max_tokens / thinking budget / temperature).

    Wires MODE_PARAMS (previously dead code) into the loop. No mode → the prior
    default (max_tokens=4096) so non-mode behavior is unchanged.
    """
    if mode:
        try:
            from src.model_router import MODE_PARAMS
            if mode in MODE_PARAMS:
                return dict(MODE_PARAMS[mode])
        except Exception:
            pass
    return {"max_tokens": 4096}


def _resolve_model(model: str, mode: str) -> str:
    """Pick the active model.

    Precedence: explicit --model wins, then --mode → MODE_MODEL_MAP,
    then KORGEX_MODEL env, then default Sonnet 4.6.
    """
    if model:
        return model
    if mode:
        try:
            from src.model_router import MODE_MODEL_MAP, DEFAULT_MODELS
            key = MODE_MODEL_MAP.get(mode)
            if key and key in DEFAULT_MODELS:
                return DEFAULT_MODELS[key].model_id
        except Exception:
            pass  # fall through to env/default
    return os.environ.get("KORGEX_MODEL", "claude-sonnet-4-6")


class KorgexAgent:
    """Provider-agnostic agent loop. Speaks both Anthropic and OpenAI tool-use shapes."""

    def __init__(self, model: str = None, repo_root: str = None,
                 mode: str = None, interactive: bool = None,
                 load_mcp: bool = None, ledger=None, **_ignored):
        # **_ignored absorbs legacy kwargs (model_override, resume_session, etc.)
        self.mode = mode
        self.model = _resolve_model(model, mode)
        self.repo_root = repo_root or os.getcwd()
        # KORGEX_PROVIDER forces the transport (overriding model-id autodetect),
        # so a Claude/Gemini model can be driven through an OpenAI-compatible
        # gateway like OpenRouter. Garbage values fall back to autodetect.
        _forced = os.environ.get("KORGEX_PROVIDER", "").strip().lower()
        if _forced in ("openai", "anthropic"):
            self.provider = _forced
        else:
            self.provider = "anthropic" if _looks_anthropic(self.model) else "openai"

        # Per-mode generation params (max_tokens / thinking budget / temperature).
        self.params = _resolve_params(mode)

        # Injectable ledger client. None → resolve the process default lazily in
        # run_task. A subagent is handed its parent's ledger so events from the
        # whole multi-agent run land in one causal journal.
        self.ledger = ledger

        # Injectable hook table. None → load from .korgex/settings.json in run_task.
        self.hooks = None

        # Factory used by the Agent tool to build a child agent. None → a real
        # nested KorgexAgent. Overridable in tests / for custom subagent runtimes.
        self.subagent_factory = None

        # Effective system prompt. Recomputed per run_task from the base prompt +
        # project instructions (AGENTS.md/CLAUDE.md) + persistent memory index.
        self.system_prompt = SYSTEM_PROMPT

        # Workspace isolation (Gate A): when set, Write/Edit whose resolved path
        # escapes this root are blocked. Set by run_isolated_task to a worktree.
        self.workspace_root = None

        # Guardrail fence (Gate G): a list of protected path patterns. When set,
        # Write/Edit to a guardrail-critical file is blocked (PROTECTED_PATH) so
        # an unsupervised run can't weaken its own gates.
        self.protected_paths = None

        # Test gate (Gate B): {"command": "pytest -q ..."} → after a run that
        # mutated files, the suite runs and a red result forces success=False.
        self.test_gate = None

        # Auto-heal (idea #8, opt-in / explicit): when the test gate is red and
        # heal_attempts > 0, spawn heal_fn(failure_output, cwd) and re-run the
        # gate up to heal_attempts times. Each attempt + the verdict is recorded
        # to the (hash-chained) ledger as a verifiable repair trail.
        self.heal_attempts = 0
        self.heal_fn = None

        # Edit-approval policy (consulted before any file-mutating tool runs).
        # WORKSPACE = auto-approve inside the repo/tmp, confirm sensitive +
        # outside-repo; SESSION = auto-approve; ASK = confirm every edit. Hard-
        # blocked paths (.git/.ssh/.gnupg) are always refused. Every decision is
        # recorded to the ledger; an approved edit in an isolated worktree is
        # checkpointed-before-mutation (revertable). $KORGEX_EDIT_POLICY overrides.
        self.edit_policy = (os.environ.get("KORGEX_EDIT_POLICY") or _EP.WORKSPACE).strip().lower()
        # Optional confirmer(path)->bool for interactive approval; None → the
        # headless fail-safe (sensitive blocked; ordinary outside-workspace
        # proceeds-and-records so automation isn't broken).
        self._edit_confirmer = None

        # In-process plugin registry — complements the shell command-hooks
        # (src/hooks.py) with low-latency Python observers on the agent lifecycle
        # (on_user_prompt / pre_tool / post_tool / on_stop). Empty → zero overhead;
        # a plugin that raises is isolated and can never break the loop.
        self.plugins = PluginRegistry()

        # Opt-in LSP auto-diagnostics: after a Write/Edit, a language server checks
        # the file and its findings are folded back into the edit's result, so the
        # agent sees the errors it just introduced mid-loop. Needs a server
        # installed (no-op otherwise). $KORGEX_LSP_DIAGNOSTICS=1 enables it.
        if os.environ.get("KORGEX_LSP_DIAGNOSTICS", "").strip().lower() in ("1", "true", "yes", "on"):
            from src.lsp import post_tool_plugin
            self.plugins.register("post_tool", post_tool_plugin)

        # Opt-in LSP ENFORCEMENT (Gate L): promote diagnostics from advisory to a
        # hard-block. With $KORGEX_LSP_ENFORCE on, a Write/Edit that introduces a
        # SEVERITY-1 (error) diagnostic is REFUSED — the file is reverted to its
        # pre-edit state and a verifiable `lsp.enforce` policy event is recorded,
        # so the model must fix-or-revert before proceeding. Default OFF: the
        # diagnostics still get folded into the result as before, nothing is vetoed.
        self.lsp_enforce = os.environ.get(
            "KORGEX_LSP_ENFORCE", "").strip().lower() in ("1", "true", "yes", "on")

        # Interactive (streaming TUI) on by default when stdout is a TTY,
        # off when redirected (so tests and pipes get clean stdout).
        if interactive is None:
            interactive = sys.stdout.isatty()
        self.interactive = interactive

        # MCP loading opt-in: env var or explicit kwarg. Default off because
        # mcp.json may reference servers (npx, GITHUB_TOKEN) that aren't ready.
        if load_mcp is None:
            load_mcp = os.environ.get("KORGEX_MCP", "").strip().lower() in ("1", "true", "yes")
        if load_mcp:
            self._load_mcp_servers()

        # Lazy: only construct the session when actually streaming
        self._session = None

    def _assemble_system_prompt(self) -> str:
        """Base prompt + project instructions + persistent memory index.

        Reads AGENTS.md/CLAUDE.md and an EXISTING memory store — never creates a
        memory dir as a side effect of running a task.
        """
        parts = [SYSTEM_PROMPT]

        # Project instructions: AGENTS.md (preferred) or CLAUDE.md.
        for fname in ("AGENTS.md", "CLAUDE.md"):
            path = os.path.join(self.repo_root, fname)
            if os.path.isfile(path):
                try:
                    content = open(path).read().strip()
                except OSError:
                    content = ""
                if content:
                    parts.append(f"# Project instructions ({fname})\n\n{content}")
                break

        # Persistent memory index, if one already exists.
        for mem_root in (os.path.join(self.repo_root, ".korgex", "memory"),
                         os.path.join(os.path.expanduser("~"), ".korgex", "memory")):
            idx = os.path.join(mem_root, "MEMORY.md")
            if os.path.isfile(idx):
                try:
                    content = open(idx).read().strip()
                except OSError:
                    content = ""
                if content and content != "# Memory Index":
                    parts.append(f"# Memory\n\n{content[:3000]}")
                break

        return "\n\n".join(parts)

    def _recall_and_reconcile(self, korg, prompt_seq) -> str:
        """Recall persistent memories, verify each anchored one against its source
        baseline, and return a trusted-memory prompt block of the FRESH facts —
        withholding stale ones and recording a `memory_reconcile` decision to the
        (hash-chained) ledger for each drift (idea #5: auditable memory). Returns
        "" when there's no memory store. Never creates a memory dir, and NEVER
        raises — recall is an enhancement, not core, so any failure (a missing
        optional dependency, an unreadable store) degrades to no recall rather
        than crashing the agent loop.
        """
        try:
            from src import memory as M
            from src import memory_drift as D

            mem_root = None
            for cand in (os.path.join(self.repo_root, ".korgex", "memory"),
                         os.path.join(os.path.expanduser("~"), ".korgex", "memory")):
                if os.path.isdir(cand):
                    mem_root = cand
                    break
            if not mem_root:
                return ""

            prev = M.MEMORY_DIR
            M.MEMORY_DIR = mem_root  # point the lister at the existing store (no creation)
            try:
                memories = M.list_memories()
            finally:
                M.MEMORY_DIR = prev
            if not memories:
                return ""

            out = D.recall_block(
                memories, repo_root=self.repo_root,
                record_event=lambda tn, a, r, s, tb: korg.record_tool_call(
                    tool_name=tn, args=a, result=r, success=s, duration_ms=0, triggered_by=tb),
                triggered_by=prompt_seq)
            return out["block"]
        except Exception:
            return ""  # recall must never break the agent loop

    def _get_session(self):
        """Create the InteractiveSession on demand (avoids Rich import in non-TTY runs)."""
        if self._session is None and self.interactive:
            from src.interactive import InteractiveSession
            self._session = InteractiveSession()
        return self._session

    def _load_mcp_servers(self) -> int:
        """Boot every MCP server in mcp.json and register their tools into USER_TOOLS.

        Failures are logged but never crash agent startup.
        Returns the number of tools registered.
        """
        try:
            from src.mcp_client import load_mcp_config, get_manager
            from src.tool_abstraction import register_mcp_tool
        except Exception as e:
            print(f"[mcp] client unavailable: {e}", file=sys.stderr)
            return 0

        configs = load_mcp_config()
        if not configs:
            return 0

        manager = get_manager()
        registered = 0
        for name, cfg in configs.items():
            result = manager.add_server(cfg)
            if "error" in result:
                print(f"[mcp] skipping {name}: {result['error']}", file=sys.stderr)
                continue
            for tool in manager.get_all_tools():
                if tool.server_name == name:
                    register_mcp_tool(tool)
                    registered += 1
        if self.interactive and registered:
            print(f"[mcp] loaded {registered} tool(s) from {len(configs)} server(s)", file=sys.stderr)
        return registered

    # ── Tool schema translation ──────────────────────────────────────────

    def _get_provider_tools(self, tools_filter=None) -> list[dict]:
        """Translate USER_TOOLS into the schema shape the provider expects.

        `tools_filter` (a set/list of tool names) restricts the exposed tools —
        used to give a subagent a narrower surface than the parent.
        """
        if tools_filter is None:
            items = list(USER_TOOLS.values())
        else:
            allow = set(tools_filter)
            items = [t for n, t in USER_TOOLS.items() if n in allow]

        if self.provider == "anthropic":
            return [{
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            } for t in items]

        # OpenAI-compatible: openai, openrouter, ollama, deepseek, etc.
        return [{
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        } for t in items]

    # ── Client wiring ────────────────────────────────────────────────────

    def _get_client(self):
        if self.provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("KORGEX_API_KEY")
            if not key:
                raise RuntimeError(
                    "No API key found. Set ANTHROPIC_API_KEY (preferred) or KORGEX_API_KEY."
                )
            from anthropic import Anthropic
            return Anthropic(api_key=key)

        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("KORGEX_API_KEY")
        if not key:
            raise RuntimeError(
                "No API key found. Set OPENAI_API_KEY (preferred) or KORGEX_API_KEY."
            )
        from openai import OpenAI
        return OpenAI(
            api_key=key,
            base_url=os.environ.get("KORGEX_API_URL", "https://api.openai.com/v1"),
        )

    def _gen_kwargs(self) -> dict:
        """Per-mode generation kwargs for the active provider. max_tokens always;
        Anthropic gets a thinking budget if the mode sets one (temperature is
        omitted then — they're mutually exclusive); otherwise temperature."""
        p = self.params or {}
        kw = {"max_tokens": p.get("max_tokens", 4096)}
        if self.provider == "anthropic":
            th = p.get("thinking")
            if th and th.get("budget_tokens"):
                kw["thinking"] = {"type": "enabled", "budget_tokens": th["budget_tokens"]}
            elif p.get("temperature") is not None:
                kw["temperature"] = p["temperature"]
        else:  # openai-compatible — no thinking param
            if p.get("temperature") is not None:
                kw["temperature"] = p["temperature"]
        return kw

    def _call(self, client, messages: list, tools: list, output_schema: dict = None,
              system_prompt: str = None) -> object:
        # `system_prompt` is passed explicitly (not read off self) so concurrent
        # run_task calls on one agent instance can't clobber each other's prompt.
        sp = system_prompt if system_prompt is not None else self.system_prompt
        gen = self._gen_kwargs()

        # Schema-constrained final answer: force a non-streamed, structured reply.
        # (You can't render a partial validated object, so streaming is bypassed
        # when output_schema is set.) Thinking is dropped here — a forced single
        # tool call doesn't need a thinking budget and can conflict with it.
        if output_schema is not None:
            from src.structured_output import build_request_kwargs
            extra = build_request_kwargs(output_schema, self.provider)
            max_tokens = gen.get("max_tokens", 4096)
            if self.provider == "anthropic":
                return client.messages.create(
                    model=self.model, system=sp,
                    messages=messages, max_tokens=max_tokens, **extra,
                )
            return client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=max_tokens, **extra,
            )

        # Interactive streaming paths
        if self.interactive and self.provider == "anthropic":
            return self._call_anthropic_streaming(client, messages, tools, sp)
        if self.interactive and self.provider == "openai":
            return self._call_openai_streaming(client, messages, tools)

        # Non-streaming
        if self.provider == "anthropic":
            return client.messages.create(
                model=self.model, system=sp,
                messages=messages, tools=tools, **gen,
            )
        return client.chat.completions.create(
            model=self.model, messages=messages,
            tools=tools, **gen,
        )

    def _call_anthropic_streaming(self, client, messages: list, tools: list,
                                  system_prompt: str = None):
        """Stream Anthropic messages through the InteractiveSession renderer."""
        from src.interactive import SSEMessage, SSEEvent
        session = self._get_session()

        with client.messages.stream(
            model=self.model, system=(system_prompt if system_prompt is not None else self.system_prompt),
            messages=messages, tools=tools, max_tokens=4096,
        ) as stream:
            for event in stream:
                ev_type = getattr(event, "type", None)
                if not ev_type:
                    continue
                try:
                    sse_event = SSEEvent(ev_type)
                except ValueError:
                    continue  # unknown event type — skip rather than crash render
                try:
                    data = event.model_dump() if hasattr(event, "model_dump") else {}
                except Exception:
                    data = {}
                if session.stream_event(SSEMessage(event=sse_event, data=data)):
                    break  # user interrupted

            # get_final_message gives us the same shape as messages.create()
            return stream.get_final_message()

    def _call_openai_streaming(self, client, messages: list, tools: list):
        """Stream OpenAI/OpenRouter chunks; render text live; accumulate tool calls.

        Returns a stub object shaped like a non-streamed ChatCompletion so the
        rest of the loop (_extract_tool_calls, _assistant_turn) works unchanged.
        """
        from src.interactive import SSEMessage, SSEEvent
        session = self._get_session()

        # Pump-through state
        full_text = ""
        # idx → {"id", "name", "args_str"}
        partials: dict[int, dict] = {}

        stream = client.chat.completions.create(
            model=self.model, messages=messages,
            tools=tools, max_tokens=4096, stream=True,
        )

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Text token → synthesize an Anthropic-style text_delta event for the renderer
            text_piece = getattr(delta, "content", None) or ""
            if text_piece:
                full_text += text_piece
                sse = SSEMessage(
                    event=SSEEvent.CONTENT_BLOCK_DELTA,
                    data={"delta": {"type": "text_delta", "text": text_piece}},
                )
                if session.stream_event(sse):
                    break

            # Tool call deltas arrive as partial JSON across multiple chunks, keyed by index
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = tc.index
                slot = partials.setdefault(idx, {"id": None, "name": "", "args_str": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if fn.name:
                        slot["name"] = fn.name
                    if fn.arguments:
                        slot["args_str"] += fn.arguments

        # Build a fake response object shaped like a non-streamed ChatCompletion
        return _StubOpenAIResponse(full_text, partials)

    # ── Response parsing ─────────────────────────────────────────────────

    def _extract_tool_calls(self, response) -> list[dict]:
        """Return a normalized list: [{id, name, args}, ...]."""
        if response is None:
            return []

        if self.provider == "anthropic":
            calls = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    calls.append({
                        "id": block.id,
                        "name": block.name,
                        "args": block.input or {},
                    })
            return calls

        msg = response.choices[0].message
        calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        return calls

    def _extract_final_text(self, response) -> str:
        """Pull the assistant's text content for the no-tool-call return."""
        if response is None:
            return ""
        if self.provider == "anthropic":
            parts = [getattr(b, "text", "") for b in response.content
                     if getattr(b, "type", None) == "text"]
            return "".join(parts).strip()
        return (response.choices[0].message.content or "").strip()

    def _assistant_turn(self, response) -> dict:
        """Convert an LLM response into a message dict suitable for re-feeding."""
        if self.provider == "anthropic":
            # Re-hydrate the raw content blocks so the API sees its own output verbatim
            content = []
            for b in response.content:
                if hasattr(b, "model_dump"):
                    content.append(b.model_dump())
                elif hasattr(b, "dict"):
                    content.append(b.dict())
                else:
                    content.append({"type": getattr(b, "type", "text"),
                                    "text": getattr(b, "text", "")})
            return {"role": "assistant", "content": content}

        msg = response.choices[0].message
        return {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in (msg.tool_calls or [])
            ],
        }

    def _tool_result_turn(self, tool_id: str, result: dict) -> dict:
        content = json.dumps(result, default=str)
        if self.provider == "anthropic":
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                }],
            }
        return {"role": "tool", "tool_call_id": tool_id, "content": content}

    # ── Main loop ────────────────────────────────────────────────────────

    def run_task(self, prompt: str, output_schema: dict = None,
                 parent_seq: int = None, tools_filter=None) -> dict:
        korg = self.ledger if self.ledger is not None else _korg()
        hooks = self.hooks if self.hooks is not None else load_hooks(self.repo_root)
        # Memory injection: assemble base + AGENTS.md + memory. Held in a LOCAL and
        # threaded through _call so concurrent run_task calls (korgantic fan-out)
        # can't clobber each other via shared self.system_prompt. The attribute is
        # still updated for introspection/back-compat, but is never the read source.
        sys_prompt = self._assemble_system_prompt()
        self.system_prompt = sys_prompt

        # UserPromptSubmit hooks may inject context (advisory; cannot block).
        # The ledger records the ORIGINAL prompt; only the model's view is augmented.
        effective_prompt = prompt
        if hooks:
            ups = run_event(
                "UserPromptSubmit", "",
                {"event": "UserPromptSubmit", "prompt": prompt, "cwd": self.repo_root},
                hooks, cwd=self.repo_root,
            )
            if ups.get("additional_context"):
                effective_prompt = f"{prompt}\n\n[hook context]\n{ups['additional_context']}"

        tools_payload = self._get_provider_tools(tools_filter)

        if self.provider == "anthropic":
            messages = [{"role": "user", "content": effective_prompt}]
        else:
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": effective_prompt},
            ]

        client = self._get_client()
        max_iter = int(os.environ.get("KORGEX_MAX_ITERATIONS", "30"))

        session = self._get_session()
        if session:
            session.start()

        # ── korg ledger: root event ──────────────────────────────────────────
        # Top-level session → triggered_by=None (a true root). When this run is a
        # SUBAGENT, parent_seq chains its root into the parent's causal DAG so the
        # whole multi-agent run is one connected, rewindable tree.
        prompt_seq = korg.record_user_prompt(prompt, triggered_by=parent_seq)
        self.plugins.invoke("on_user_prompt", {"prompt": prompt, "seq": prompt_seq})
        # ────────────────────────────────────────────────────────────────────

        # idea #5: recall persistent memory, verify anchored facts against their
        # source baselines, inject the fresh ones, and record a memory_reconcile
        # decision (chained off prompt_seq) for any that drifted — auditable memory.
        recalled = self._recall_and_reconcile(korg, prompt_seq)
        if recalled:
            sys_prompt = sys_prompt + "\n\n" + recalled
            if self.provider != "anthropic" and messages and messages[0].get("role") == "system":
                messages[0]["content"] = sys_prompt

        self._bus_deliver_initial(messages, korg, prompt_seq)

        mutated = False  # did any file-mutating tool run? gates the test-gate
        try:
            for i in range(max_iter):
                # ── korg: time the LLM round-trip ──────────────────────────
                _llm_t0 = time.monotonic()
                response = self._call(client, messages, tools_payload, system_prompt=sys_prompt)
                _llm_ms = int((time.monotonic() - _llm_t0) * 1000)

                tool_calls = self._extract_tool_calls(response)

                # Pull the assistant's text content (if any) so we can record
                # it onto the llm_inference event. Tool-call-only rounds emit
                # an empty string here, which becomes None on the bridge call
                # and preserves the v0.3.1 shape for those events.
                round_text = self._extract_final_text(response)

                # Emit one llm_inference event per completed round-trip.
                # Parallel tool calls in this batch all use llm_seq as triggered_by
                # (they are siblings, not a chain — see agent_event_spec.md §2).
                llm_seq = korg.record_llm_call(
                    model=self.model,
                    prompt_tokens=getattr(getattr(response, "usage", None), "input_tokens", 0)
                                  or getattr(getattr(response, "usage", None), "prompt_tokens", 0),
                    completion_tokens=getattr(getattr(response, "usage", None), "output_tokens", 0)
                                      or getattr(getattr(response, "usage", None), "completion_tokens", 0),
                    duration_ms=_llm_ms,
                    triggered_by=prompt_seq,
                    assistant_text=round_text if round_text else None,
                )
                # ───────────────────────────────────────────────────────────

                if not tool_calls:
                    # Schema-constrained finish: do a final structured pass so
                    # the answer is a validated object on the ledger, not prose.
                    if output_schema is not None:
                        return self._finish(self._finalize_structured(
                            client, messages, response, output_schema,
                            llm_seq, korg, i + 1, prompt_seq, sys_prompt,
                        ), korg, prompt_seq, mutated)
                    # Reuse round_text we already extracted above; saves a
                    # second pass over response.content.
                    return self._finish({
                        "success": True,
                        "result": round_text or "(no output)",
                        "iterations": i + 1,
                        "root_seq": prompt_seq,
                    }, korg, prompt_seq, mutated)

                messages.append(self._assistant_turn(response))
                for call in tool_calls:
                    # ── Workspace boundary guard (Gate A): hard safety ───────
                    # When isolated, a Write/Edit whose resolved path escapes the
                    # workspace root is blocked outright and recorded as a
                    # WORKSPACE_VIOLATION verdict — a self-edit can't corrupt
                    # anything outside its worktree.
                    ws_block = self._workspace_block(call)
                    if ws_block is not None:
                        korg.record_tool_call(
                            tool_name="workspace.guard",
                            args={"tool": call["name"], "path": call["args"].get("file_path")},
                            result=ws_block,
                            success=False, duration_ms=0, triggered_by=llm_seq,
                        )
                        messages.append(self._tool_result_turn(call["id"], ws_block))
                        continue  # the write never happens

                    # ── Guardrail fence (Gate G): protect gate-enforcing code ─
                    gr_block = self._guardrail_block(call)
                    if gr_block is not None:
                        korg.record_tool_call(
                            tool_name="guardrail.block",
                            args={"tool": call["name"], "path": call["args"].get("file_path")},
                            result=gr_block,
                            success=False, duration_ms=0, triggered_by=llm_seq,
                        )
                        messages.append(self._tool_result_turn(call["id"], gr_block))
                        continue  # the agent can't edit its own guardrails

                    # ── Edit-approval policy + checkpoint-before-mutation ────
                    # Consult the policy before any file-mutating tool; the gate
                    # records its own verdict event and snapshots the workspace
                    # before an approved edit (in an isolated worktree).
                    ep_block = self._edit_policy_block(call, korg, llm_seq)
                    if ep_block is not None:
                        messages.append(self._tool_result_turn(call["id"], ep_block))
                        continue  # the edit was refused by the approval policy

                    # ── PreToolUse gate: deterministic, ledger-native ────────
                    # A matching hook can block the call. Every verdict (allow or
                    # deny) is recorded as its own causal event carrying the
                    # policy_hash of the rule that fired — so governance over tool
                    # calls is rewindable and auditable, not fire-and-forget.
                    if hooks:
                        pre = run_event(
                            "PreToolUse", call["name"],
                            {"event": "PreToolUse", "tool_name": call["name"],
                             "tool_input": call["args"], "cwd": self.repo_root},
                            hooks, cwd=self.repo_root,
                        )
                        if pre["ran"]:
                            verdict = "BLOCKED" if pre["decision"] == "block" else "APPROVED"
                            korg.record_tool_call(
                                tool_name="hook.PreToolUse",
                                args={"tool": call["name"]},
                                result={"verdict": verdict, "reason": pre["reason"],
                                        "policy_hash": pre["policy_hash"]},
                                success=(verdict == "APPROVED"),
                                duration_ms=0,
                                triggered_by=llm_seq,
                            )
                        if pre["decision"] == "block":
                            blocked = {"error": "blocked by PreToolUse hook",
                                       "reason": pre["reason"] or "policy denied this tool call"}
                            messages.append(self._tool_result_turn(call["id"], blocked))
                            continue  # the tool never runs

                    self.plugins.invoke("pre_tool", call)
                    # Snapshot the file's pre-edit bytes so LSP enforcement (Gate L)
                    # can revert a vetoed mutation. None = file did not exist (a
                    # create), so a revert means deleting it. Captured only when
                    # enforcement is armed — zero cost otherwise.
                    _pre_content = self._capture_pre_content(call) if self.lsp_enforce else None
                    if session:
                        # Show a transient spinner while the tool runs
                        with session.spinner(f"{call['name']}({_short_args(call['args'])})"):
                            _t0 = time.monotonic()
                            tool_result = self._dispatch_call(call, llm_seq)
                            _ms = int((time.monotonic() - _t0) * 1000)
                    else:
                        _t0 = time.monotonic()
                        tool_result = self._dispatch_call(call, llm_seq)
                        _ms = int((time.monotonic() - _t0) * 1000)

                    # ── korg: one event per completed tool call ─────────────
                    # All tool calls from the same LLM batch share triggered_by=llm_seq.
                    # They are siblings in the causal tree, not chained to each other.
                    _success = "error" not in tool_result if isinstance(tool_result, dict) else True
                    if call["name"] in ("Write", "Edit", "Bash") and _success:
                        mutated = True  # a file-mutating tool ran → arm the test gate
                    korg.record_tool_call(
                        tool_name=call["name"],
                        args=call["args"],
                        result=tool_result,
                        success=_success,
                        duration_ms=_ms,
                        triggered_by=llm_seq,
                    )
                    # ───────────────────────────────────────────────────────
                    _all_diags: list = []
                    for _pr in self.plugins.invoke("post_tool", {"call": call, "result": tool_result}):
                        # Auto-diagnostics: fold a language server's findings into the
                        # edit's result (so the LLM sees the errors it just introduced)
                        # and record them as their own ledger event.
                        if isinstance(_pr, dict) and _pr.get("diagnostics"):
                            _diags = _pr["diagnostics"]
                            _all_diags.extend(_diags)
                            if isinstance(tool_result, dict):
                                tool_result = {**tool_result, "diagnostics": _diags}
                            korg.record_tool_call(
                                tool_name="lsp.diagnostics",
                                args={"file": _pr.get("file"), "tool": call["name"]},
                                result={"count": len(_diags), "diagnostics": _diags[:10]},
                                success=not any(d.get("severity") == 1 for d in _diags),
                                duration_ms=0, triggered_by=llm_seq,
                            )

                    # ── LSP enforcement (Gate L): veto a severity-1 edit ─────
                    # Opt-in. Promotes the advisory diagnostics above into a hard
                    # block: a Write/Edit that introduced a language-server ERROR is
                    # reverted to its pre-edit bytes and refused with a fix-or-revert
                    # message, recorded as a verifiable `lsp.enforce` policy event.
                    _veto = self._lsp_enforce_block(call, _all_diags, korg, llm_seq, _pre_content)
                    if _veto is not None:
                        tool_result = _veto

                    # ── PostToolUse hook (advisory; cannot undo the call) ────
                    if hooks:
                        post = run_event(
                            "PostToolUse", call["name"],
                            {"event": "PostToolUse", "tool_name": call["name"],
                             "tool_input": call["args"], "tool_result": tool_result,
                             "cwd": self.repo_root},
                            hooks, cwd=self.repo_root,
                        )
                        if post["ran"]:
                            korg.record_tool_call(
                                tool_name="hook.PostToolUse",
                                args={"tool": call["name"]},
                                result={"verdict": "OBSERVED",
                                        "policy_hash": post["policy_hash"]},
                                success=True, duration_ms=0, triggered_by=llm_seq,
                            )

                    messages.append(self._tool_result_turn(call["id"], tool_result))

                # Advance the LLM trigger for the next round-trip to the last llm_seq
                prompt_seq = llm_seq

            return self._finish({
                "success": False,
                "result": f"max iterations reached ({max_iter})",
                "iterations": max_iter,
                "root_seq": prompt_seq,
            }, korg, prompt_seq, mutated)
        finally:
            if session:
                session.stop()

    def _finish(self, result: dict, korg, prompt_seq, mutated: bool) -> dict:
        """Apply the test gate (Gate B) on a successful, file-mutating run.

        Runs the configured test command in the workspace; a red result flips
        success to False (the edit is NOT accepted) and records a verdict event.
        No gate, no edits, or an already-failed run → returned unchanged.
        """
        self.plugins.invoke("on_stop", result)
        gate = self.test_gate
        if not (gate and gate.get("command") and mutated and result.get("success")):
            return result

        from src.test_gate import run_test_gate
        cwd = self.workspace_root or self.repo_root
        g = run_test_gate(gate["command"], cwd=cwd, timeout=gate.get("timeout", 600))
        gate_seq = korg.record_tool_call(
            tool_name="test_gate",
            args={"command": gate["command"]},
            result={"verdict": "PASSED" if g["passed"] else "FAILED",
                    "exit_code": g["exit_code"], "output": g["output"][:4000]},
            success=g["passed"], duration_ms=0, triggered_by=prompt_seq,
        )
        result = dict(result)

        # idea #8: on red, attempt a bounded auto-heal-to-green, recording each
        # attempt as a chained ledger event. Opt-in (heal_attempts + heal_fn set).
        if not g["passed"] and self.heal_attempts and self.heal_fn:
            from src.self_healing import auto_heal_to_green
            g = auto_heal_to_green(
                g,
                run_gate=lambda: run_test_gate(gate["command"], cwd=cwd,
                                               timeout=gate.get("timeout", 600)),
                heal_fn=lambda output: self.heal_fn(output, cwd),
                record_event=lambda tn, a, r, s, tb: korg.record_tool_call(
                    tool_name=tn, args=a, result=r, success=s, duration_ms=0, triggered_by=tb),
                max_attempts=self.heal_attempts, triggered_by=gate_seq,
            )

        result["test_gate"] = {"passed": g["passed"], "exit_code": g["exit_code"],
                               "output": g["output"][:4000]}
        if not g["passed"]:
            result["success"] = False
            result["result"] = (f"test gate failed (exit {g['exit_code']}) — edit not "
                                f"accepted. Output:\n{g['output'][:2000]}")
        return result

    def _dispatch_call(self, call: dict, parent_seq) -> dict:
        """Run one tool call. The Agent tool spawns a real nested subagent;
        every other tool routes to its in-process / MCP handler. File/Bash tools
        resolve under the workspace root (the isolated worktree) when set."""
        if call["name"] == "Agent":
            return self._run_subagent(call["args"], parent_seq)
        return route_tool_call(call["name"], call["args"],
                               repo_root=self.workspace_root or self.repo_root)

    def _workspace_block(self, call: dict):
        """Return a blocked-result dict if `call` would write outside the
        workspace root, else None. Only active when workspace_root is set."""
        if not self.workspace_root:
            return None
        if call.get("name") not in ("Write", "Edit"):
            return None
        path = (call.get("args") or {}).get("file_path")
        if path and not path_within(self.workspace_root, path):
            return {
                "error": "blocked: write outside the isolated workspace",
                "verdict": "WORKSPACE_VIOLATION",
                "reason": f"{path} resolves outside workspace_root {self.workspace_root}",
            }
        return None

    def _guardrail_block(self, call: dict):
        """Return a blocked-result dict if `call` would edit a guardrail-critical
        file, else None. Only active when protected_paths is set (Gate G)."""
        if not self.protected_paths:
            return None
        if call.get("name") not in ("Write", "Edit"):
            return None
        path = (call.get("args") or {}).get("file_path")
        if path and is_protected(path, self.protected_paths):
            return {
                "error": "blocked: editing a guardrail-critical file requires human approval",
                "verdict": "PROTECTED_PATH",
                "reason": f"{path} is a protected guardrail file (Gate G)",
            }
        return None

    def _edit_policy_block(self, call: dict, korg, llm_seq):
        """Edit-approval gate. For a file-mutating tool: consult the policy, record
        the decision to the ledger, and checkpoint the workspace BEFORE an approved
        mutation. Returns a blocked-result dict if the edit is refused, else None.
        Non-file-mutating calls pass straight through (returns None, records nothing)."""
        args = call.get("args") or {}
        path = _EP.mutating_path(call.get("name"), args)
        if path is None:
            return None
        proceed, action, reason = _EP.guard_decision(
            path, policy=self.edit_policy, cwd=self.repo_root,
            interactive=self.interactive, confirmer=self._edit_confirmer,
        )
        sha = self._checkpoint_before_mutation(path) if proceed else None
        korg.record_tool_call(
            tool_name="edit_policy",
            args={"tool": call.get("name"), "path": path, "policy": self.edit_policy},
            result={"action": action, "reason": reason, "allowed": proceed, "checkpoint": sha},
            success=proceed, duration_ms=0, triggered_by=llm_seq,
        )
        if not proceed:
            return {"error": "edit refused by approval policy",
                    "verdict": action.upper().replace("-", "_"), "reason": reason}
        return None

    def _lsp_enforce_block(self, call: dict, diagnostics, korg, llm_seq, pre_content):
        """LSP enforcement gate (Gate L) — opt-in hard-block on severity-1 errors.

        After a file-mutating tool ran and a language server returned diagnostics,
        this decides whether to VETO. It blocks only when ALL hold:
          - enforcement is enabled (self.lsp_enforce / $KORGEX_LSP_ENFORCE);
          - the tool is file-mutating (Write/Edit/MultiEdit/NotebookEdit);
          - at least one diagnostic is SEVERITY 1 (an error, not a warning/hint).

        On a veto it REVERTS the file to `pre_content` (or deletes it if the edit
        created it: `pre_content is None`), records a verifiable `lsp.enforce`
        policy event, and returns a fix-or-revert block dict for the tool result.
        Otherwise returns None (advisory behavior is unchanged). Mirrors
        `_edit_policy_block`: pure decision + one ledger event + a block dict."""
        if not self.lsp_enforce:
            return None
        path = _EP.mutating_path(call.get("name"), call.get("args") or {})
        if path is None or not diagnostics:
            return None
        errors = [d for d in diagnostics if d.get("severity") == 1]
        if not errors:
            return None  # warnings/hints are advisory — only errors veto

        # Resolve under the workspace root exactly as the tools do, so the revert
        # targets the same file that was written (matches _capture_pre_content).
        root = self.workspace_root or self.repo_root
        resolved = (os.path.join(root, path)
                    if root and not os.path.isabs(path) else path)

        # Revert the offending edit so a vetoed write never lands on disk.
        action = self._revert_mutation(resolved, pre_content)

        messages = [d.get("message", "") for d in errors[:5]]
        block = {
            "error": (f"edit refused: introduced {len(errors)} language-server "
                      f"error(s) — fix the cause or revert. The change was reverted."),
            "verdict": "LSP_SEVERITY_1",
            "reason": f"severity-1 diagnostic(s) in {path}: " + "; ".join(messages),
            "diagnostics": errors[:10],
        }
        korg.record_tool_call(
            tool_name="lsp.enforce",
            args={"tool": call.get("name"), "path": path},
            result={"verdict": "REFUSED", "action": action,
                    "error_count": len(errors), "messages": messages},
            success=False, duration_ms=0, triggered_by=llm_seq,
        )
        return block

    def _capture_pre_content(self, call: dict):
        """Read a file-mutating tool's target BEFORE it runs, so a vetoed edit can
        be reverted. Returns the current text, or None if the path is not a
        mutating target or the file doesn't exist yet (a create → revert deletes).
        Resolves under the workspace root when isolated, matching the tools."""
        path = _EP.mutating_path(call.get("name"), call.get("args") or {})
        if path is None:
            return None
        root = self.workspace_root or self.repo_root
        if root and not os.path.isabs(path):
            path = os.path.join(root, path)
        try:
            with open(path) as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def _revert_mutation(self, path: str, pre_content):
        """Undo a file mutation: restore `pre_content`, or delete the file if the
        edit created it (`pre_content is None`). Best-effort; returns the action
        taken ("reverted" | "revert_failed") for the ledger record."""
        try:
            if pre_content is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w") as f:
                    f.write(pre_content)
            return "reverted"
        except OSError:
            return "revert_failed"

    def _checkpoint_before_mutation(self, path: str):
        """Best-effort git snapshot before a mutation so the edit is revertable.
        Only active in an ISOLATED worktree (workspace_root set) — where checkpoint
        commits land on a throwaway branch, never the user's working branch. Returns
        the checkpoint SHA, or None (non-fatal; the decision is still recorded)."""
        root = self.workspace_root
        if not root:
            return None
        try:
            from src.workspace import git_checkpoint
            return git_checkpoint(root, message=f"korgex-pre-edit:{os.path.basename(path)}")
        except Exception:
            return None

    def _bus_deliver_initial(self, messages, korg, trigger_seq):
        """Auto-deliver pending verifiable-bus messages into the prompt at task start,
        so the agent acts on incoming coordination without being asked. Marks them
        read and records a bus.deliver event. No-op unless a bus identity is set
        ($KORG_BUS_JOURNAL + $KORG_BUS_AGENT). Best-effort — never breaks the loop."""
        journal, me = os.environ.get("KORG_BUS_JOURNAL"), os.environ.get("KORG_BUS_AGENT")
        if not (journal and me):
            return
        try:
            from src import bus
            unread = bus.inbox(journal, me)
            if not unread:
                return
            bus.mark_read(journal, me, [m["seq"] for m in unread])
            note = ("\n\n📨 Pending messages on the verifiable agent bus — act on these as "
                    "needed and reply with the BusSend tool:\n"
                    + "\n".join(f"- from {m['from']}: {m['body']}" for m in unread))
            for msg in reversed(messages):
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    msg["content"] += note
                    break
            korg.record_tool_call(tool_name="bus.deliver",
                                  args={"agent": me, "count": len(unread)},
                                  result={"from": [m["from"] for m in unread]},
                                  success=True, duration_ms=0, triggered_by=trigger_seq)
        except Exception:
            pass

    def run_isolated_task(self, task: str, branch: str = None, worktree_path: str = None,
                          base: str = "HEAD", test_gate: dict = None,
                          protect_guardrails: bool = False, **run_kwargs) -> dict:
        """Run a task in an ISOLATED git worktree (Gate A) — the safe way to let
        korgex edit a repo autonomously. Creates a worktree on a throwaway branch,
        points all tools + the workspace guard at it, optionally enforces a test
        gate (Gate B) and a guardrail fence (Gate G), and LEAVES the branch for
        human review (never auto-merges). Returns the run result plus {worktree,
        branch, merge_gate} — merge_gate flags whether the resulting diff is
        auto-mergeable or requires human review (touches guardrail code).
        """
        from src import workspace as W
        from src.guardrails import classify_diff, DEFAULT_PROTECTED

        repo_root = self.repo_root
        branch = branch or ("korgex/" + W._slug(task)[:40])
        wt = W.create_worktree(repo_root, branch, worktree_path=worktree_path, base=base)

        prev = (self.workspace_root, self.repo_root, self.test_gate, self.protected_paths)
        self.workspace_root = wt
        self.repo_root = wt  # tools resolve here; bash runs here
        if test_gate is not None:
            self.test_gate = test_gate
        if protect_guardrails:
            self.protected_paths = DEFAULT_PROTECTED
        try:
            result = self.run_task(task, **run_kwargs)
            changed = W.changed_paths(wt)
        finally:
            self.workspace_root, self.repo_root, self.test_gate, self.protected_paths = prev

        result["worktree"] = wt
        result["branch"] = branch
        result["merge_gate"] = classify_diff(changed)
        return result

    def _run_subagent(self, args: dict, parent_seq) -> dict:
        """Spawn a real nested KorgexAgent for the Agent tool.

        The child shares this run's ledger and chains its root under parent_seq,
        so the multi-agent run is one causal DAG. It gets a tool subset scoped to
        its subagent_type and cannot itself spawn subagents (no Agent tool).
        """
        sub_type = args.get("subagent_type", "code")
        prompt = args.get("prompt") or args.get("description") or ""
        model = _MODEL_ALIASES.get(args.get("model")) or self.model

        factory = self.subagent_factory or (lambda **kw: KorgexAgent(**kw))
        child = factory(
            model=model, repo_root=self.repo_root,
            interactive=False, ledger=(self.ledger if self.ledger is not None else _korg()),
        )
        child_result = child.run_task(
            prompt, parent_seq=parent_seq, tools_filter=subagent_tools(sub_type),
        )
        return {
            "agent_type": sub_type,
            "success": child_result.get("success", False),
            "result": child_result.get("result", ""),
            "iterations": child_result.get("iterations"),
            "root_seq": child_result.get("root_seq"),
        }

    def run_korgantic_task(self, task: str, effort: str = "auto") -> dict:
        """Max-power mode: run the effort-scaled korgantic workflow chain.

        Each phase (understand/design/implement/review/verify/critic) runs as a
        run_task chained under ONE korgantic root seq, so the whole run is a
        single causal DAG in the ledger — rewindable per phase. Analysis phases
        get a read-only tool surface; implement gets the full toolset.
        """
        from src.korgantic import run_korgantic
        from src.korg_ledger import ThreadSafeLedger

        base = self.ledger if self.ledger is not None else _korg()
        # Wrap in a thread-safe ledger so concurrent phases (multi-modal sweep,
        # fan-out) can't race seq/triggered_by and corrupt the causal DAG.
        safe = ThreadSafeLedger(base)
        prev_ledger = self.ledger
        self.ledger = safe
        read_only = {"understand", "design", "review", "verify", "critic"}

        try:
            root_seq = safe.record_user_prompt(f"[korgantic:{effort}] {task}")

            def runner(role, prompt, output_schema=None):
                tools_filter = subagent_tools("explore") if role in read_only else None
                return self.run_task(
                    prompt, output_schema=output_schema,
                    parent_seq=root_seq, tools_filter=tools_filter,
                )

            result = run_korgantic(task, effort, runner)
            result["root_seq"] = root_seq
            return result
        finally:
            self.ledger = prev_ledger

    def _finalize_structured(self, client, messages: list, last_response,
                             output_schema: dict, prior_llm_seq, korg,
                             iterations: int, root_seq=None, system_prompt=None) -> dict:
        """Coerce the conversation's final answer into a schema-conforming object.

        Runs a forced structured call, validates client-side, retries once with
        the validation errors, then records the validated object onto a final
        llm_inference ledger event (so the journal carries structured data, not
        prose). Returns success=False — never a lie — if it still doesn't conform.
        """
        from src.structured_output import extract, validate

        convo = list(messages)
        convo.append(self._assistant_turn(last_response))
        convo.append({
            "role": "user",
            "content": "Return your final result now as a single object that "
                       "conforms exactly to the required schema.",
        })

        obj = None
        errors = ["no structured object returned"]
        for _attempt in range(2):  # initial + one retry
            resp = self._call(client, convo, [], output_schema=output_schema,
                              system_prompt=system_prompt)
            obj = extract(resp, self.provider)
            errors = validate(obj, output_schema) if obj is not None else \
                ["no structured object returned"]
            if not errors:
                break
            convo.append({"role": "assistant",
                          "content": json.dumps(obj) if obj is not None else ""})
            convo.append({
                "role": "user",
                "content": f"That did not conform to the schema. Fix these "
                           f"errors and re-emit the object: {errors}",
            })

        text = json.dumps(obj) if obj is not None else "{}"
        korg.record_llm_call(
            model=self.model, prompt_tokens=0, completion_tokens=0,
            duration_ms=0, triggered_by=prior_llm_seq, assistant_text=text,
        )

        if not errors:
            return {"success": True, "result": obj, "iterations": iterations,
                    "root_seq": root_seq}
        return {
            "success": False,
            "result": f"structured output failed schema validation: {errors}",
            "iterations": iterations,
            "root_seq": root_seq,
        }


def _short_args(args: dict) -> str:
    """One-line, truncated arg display for spinners."""
    if not args:
        return ""
    s = ", ".join(f"{k}={str(v)[:30]}" for k, v in args.items())
    return s[:60] + ("…" if len(s) > 60 else "")


# ── Stub response objects for OpenAI streaming ──────────────────────────
# These mimic ChatCompletion shape so _extract_tool_calls / _assistant_turn
# work uniformly across streamed and non-streamed OpenAI responses.

class _StubOpenAIResponse:
    def __init__(self, text: str, partials: dict[int, dict]):
        tool_calls = []
        for idx in sorted(partials.keys()):
            slot = partials[idx]
            if not slot["name"]:
                continue
            tool_calls.append(_StubToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=slot["args_str"] or "{}",
            ))
        self.choices = [_StubChoice(_StubMessage(text or None, tool_calls))]


class _StubChoice:
    def __init__(self, message):
        self.message = message


class _StubMessage:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class _StubToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _StubFunction(name, arguments)


class _StubFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


def print_tool_schemas():
    """Print all user-facing tool schemas in JSON."""
    from src.tool_abstraction import get_user_tool_schemas
    print(json.dumps(get_user_tool_schemas(), indent=2))


def main():
    """Entry point when run as module."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Korgex — Autonomous AI Software Engineer")
    parser.add_argument("task", nargs="?", help="Task description")
    parser.add_argument("--repo", "-r", help="Repository root path")
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument("--schemas", action="store_true", help="Print tool schemas and exit")
    parser.add_argument("--init", action="store_true", help="Initialize AGENTS.md in repo")
    
    args = parser.parse_args()
    
    if args.schemas:
        print_tool_schemas()
        return
    
    if args.init:
        agents_content = """# Korgex - Autonomous AI Software Engineer

You are Korgex, an extremely skilled software engineer.
Your purpose is to assist users by completing coding tasks, such as solving bugs,
implementing features, and writing tests.

## Core Directives
1. PLAN FIRST: Explore the codebase (list_files, read_file). Read this file and README.md.
   Ask clarifying questions. Articulate the plan using set_plan.
2. VERIFY WORK: After every modification, use read_file or list_files to confirm success.
   Do NOT mark a plan step complete until you've verified.
3. EDIT SOURCE, NOT ARTIFACTS: If a file is a build artifact (dist/, build/, node_modules/,
   __pycache__/, .next/), trace back to its source.
4. PROACTIVE TESTING: Find and run relevant tests. Plans should include testing steps.
5. DIAGNOSE BEFORE CHANGING: Read error logs and configs before installing packages.
6. SOLVE AUTONOMOUSLY: Ask only if ambiguous, stuck after multiple attempts, or scope-changing.

## Git Merge Diff Format
Use SEARCH/REPLACE blocks with exact markers:
```
<<<<<<< SEARCH
  old code here
=======
  new code here
>>>>>>> REPLACE
```

## Plan Format
Numbered steps with Markdown. Include a pre-commit step described as:
"ensure proper testing, verification, review, and reflection are done"
Do NOT mention tool names in plan steps.
"""
        dst = os.path.join(os.getcwd(), "AGENTS.md")
        with open(dst, "w") as f:
            f.write(agents_content)
        print(f"Created {dst}")
        return
    
    if not args.task:
        parser.print_help()
        return
    
    agent = KorgexAgent(repo_root=args.repo, model=args.model)
    result = agent.run_task(args.task)
    
    print(f"\n{'='*60}")
    print(f"KORGEX RESULT ({result['iterations']} iterations)")
    print(f"{'='*60}")
    print(result["result"])


if __name__ == "__main__":
    main()