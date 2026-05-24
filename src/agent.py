"""
Korgex — Core Agent Loop (provider-agnostic).

Pipeline:
    user prompt
      → LLM (tools = model-facing schemas from USER_TOOLS)
      → tool_use blocks
      → route_tool_call → internal handlers (internal tool_* in tools_impl)
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
                 load_mcp: bool = None, **_ignored):
        # **_ignored absorbs legacy kwargs (model_override, resume_session, etc.)
        self.mode = mode
        self.model = _resolve_model(model, mode)
        self.repo_root = repo_root or os.getcwd()
        self.provider = "anthropic" if _looks_anthropic(self.model) else "openai"

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

    def _get_provider_tools(self) -> list[dict]:
        """Translate USER_TOOLS into the schema shape the provider expects."""
        if self.provider == "anthropic":
            return [{
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            } for t in USER_TOOLS.values()]

        # OpenAI-compatible: openai, openrouter, ollama, deepseek, etc.
        return [{
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        } for t in USER_TOOLS.values()]

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

    def _call(self, client, messages: list, tools: list) -> object:
        # Interactive streaming paths
        if self.interactive and self.provider == "anthropic":
            return self._call_anthropic_streaming(client, messages, tools)
        if self.interactive and self.provider == "openai":
            return self._call_openai_streaming(client, messages, tools)

        # Non-streaming
        if self.provider == "anthropic":
            return client.messages.create(
                model=self.model, system=SYSTEM_PROMPT,
                messages=messages, tools=tools, max_tokens=4096,
            )
        return client.chat.completions.create(
            model=self.model, messages=messages,
            tools=tools, max_tokens=4096,
        )

    def _call_anthropic_streaming(self, client, messages: list, tools: list):
        """Stream Anthropic messages through the InteractiveSession renderer."""
        from src.interactive import SSEMessage, SSEEvent
        session = self._get_session()

        with client.messages.stream(
            model=self.model, system=SYSTEM_PROMPT,
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

    def run_task(self, prompt: str) -> dict:
        tools_payload = self._get_provider_tools()

        if self.provider == "anthropic":
            messages = [{"role": "user", "content": prompt}]
        else:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]

        client = self._get_client()
        max_iter = int(os.environ.get("KORGEX_MAX_ITERATIONS", "30"))

        session = self._get_session()
        if session:
            session.start()

        # ── korg ledger: root event ──────────────────────────────────────────
        # Every korgex session starts with a user_prompt event at triggered_by=None.
        # All subsequent events chain back here via triggered_by.
        korg = _korg()
        prompt_seq = korg.record_user_prompt(prompt)
        # ────────────────────────────────────────────────────────────────────

        try:
            for i in range(max_iter):
                # ── korg: time the LLM round-trip ──────────────────────────
                _llm_t0 = time.monotonic()
                response = self._call(client, messages, tools_payload)
                _llm_ms = int((time.monotonic() - _llm_t0) * 1000)

                tool_calls = self._extract_tool_calls(response)

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
                )
                # ───────────────────────────────────────────────────────────

                if not tool_calls:
                    text = self._extract_final_text(response)
                    return {
                        "success": True,
                        "result": text or "(no output)",
                        "iterations": i + 1,
                    }

                messages.append(self._assistant_turn(response))
                for call in tool_calls:
                    if session:
                        # Show a transient spinner while the tool runs
                        with session.spinner(f"{call['name']}({_short_args(call['args'])})"):
                            _t0 = time.monotonic()
                            tool_result = route_tool_call(call["name"], call["args"])
                            _ms = int((time.monotonic() - _t0) * 1000)
                    else:
                        _t0 = time.monotonic()
                        tool_result = route_tool_call(call["name"], call["args"])
                        _ms = int((time.monotonic() - _t0) * 1000)

                    # ── korg: one event per completed tool call ─────────────
                    # All tool calls from the same LLM batch share triggered_by=llm_seq.
                    # They are siblings in the causal tree, not chained to each other.
                    _success = "error" not in tool_result if isinstance(tool_result, dict) else True
                    korg.record_tool_call(
                        tool_name=call["name"],
                        args=call["args"],
                        result=tool_result,
                        success=_success,
                        duration_ms=_ms,
                        triggered_by=llm_seq,
                    )
                    # ───────────────────────────────────────────────────────

                    messages.append(self._tool_result_turn(call["id"], tool_result))

                # Advance the LLM trigger for the next round-trip to the last llm_seq
                prompt_seq = llm_seq

            return {
                "success": False,
                "result": f"max iterations reached ({max_iter})",
                "iterations": max_iter,
            }
        finally:
            if session:
                session.stop()


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