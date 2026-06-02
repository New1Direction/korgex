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
  /plan [on|off]  plan mode: agent stays read-only until you approve its plan
  /skills         list skills (✦ = learned by the agent) and their usage
  /skills curate  merge duplicate learned (✦) skills into one (agent-owned only)
  /tasks          show the agent's live task checklist for this conversation
  /jobs           list background shell tasks (Bash background=true) + their status
  /rewind [n]     list undo points, or restore files to BEFORE prompt n
  /clear          start a fresh conversation
  /help  /?       this help
  /exit  /quit    leave (also Ctrl-D)
anything else is a message to the agent.
tip: mention files inline with @path — e.g. "refactor @src/auth.py" inlines it.
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
        if head == "/plan":
            return Command("plan", rest or None)
        if head == "/skills":
            return Command("skills", rest or None)
        if head == "/tasks":
            return Command("tasks")
        if head == "/jobs":
            return Command("jobs")
        if head == "/rewind":
            return Command("rewind", rest or None)
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
        self._session_obj = None  # lazy prompt_toolkit session (bottom-anchored input)
        self._turn = 0           # user-prompt counter, for rewind points
        self._rewind = None      # lazy RewindLog: start-of-turn file snapshots

    def _print(self, *a):
        print(*a, file=self.out)

    def _pick_model(self):
        """Interactive model selector: show a priced, numbered menu of the
        connected providers' models (current marked), and switch to the pick."""
        from src import model_selector as _MS
        if not self.cfg.providers:
            self._print("no providers connected — run `korgex setup`")
            return
        rows = []
        for p in self.cfg.providers:
            rows.extend(_MS.menu_for(p.get("type", "")))
        if not rows:
            self._print(f"current model: {self.model}  (no suggestions; /model <id> to switch)")
            return
        self._print(_MS.render_menu(rows, current=self.model))
        try:
            answer = input("model> ")
        except (EOFError, KeyboardInterrupt):
            self._print(""); return
        choice = _MS.pick(rows, answer)
        if choice:
            self._switch_model(choice)
        else:
            self._print(f"(kept {self.model})")

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

    def _mcp_configured(self) -> bool:
        """True if any MCP servers are configured (any source). Cheap — just reads
        config files, doesn't connect."""
        import os
        try:
            from src import mcp_config
            return bool(mcp_config.load_servers(cwd=os.getcwd()))
        except Exception:
            return False

    def _ensure_agent(self):
        if self._agent is None:
            from src.agent import KorgexAgent
            # Load MCP servers when any are configured, so their tools are actually
            # available in-session (this was the gap: the REPL never enabled MCP).
            self._agent = KorgexAgent(model=self.model, interactive=True,
                                      load_mcp=self._mcp_configured())
        return self._agent

    def _show_skills(self):
        """List skills with usage + lifecycle state. ✦ marks ones korgex learned."""
        from src import skill_usage as _SU
        from src import skills as _SK
        reg = _SK.load_skills(_SK.default_skill_roots(self.repo_root))
        if not reg.names():
            self._print("no skills yet — korgex writes them as it learns (✦ agent), "
                        "or add your own under .korgex/skills/<name>/SKILL.md")
            return
        store = _SU.UsageStore(_SU.usage_path(_SU.global_skills_dir()))
        self._print("skills:")
        for r in _SU.overview(reg, store):
            mark = " ✦" if r["trust"] == "agent" else ""
            state = "" if r["state"] == "active" else f" · {r['state']}"
            self._print(f"  {r['name']}{mark}  —  {r['uses']} use(s){state}")
        if sum(1 for r in _SU.overview(reg, store) if r["trust"] == "agent") >= 2:
            self._print("→ /skills curate  merges duplicate learned (✦) skills")

    # Floor below which there's nothing worth consolidating; above it a fresh
    # learned skill triggers an auto-curation pass (new skills are rare → throttled).
    _CURATE_THRESHOLD = 8

    def _curate_skills(self, *, blocking: bool):
        """Consolidate agent-LEARNED skills: an LLM groups near-duplicates, each
        group is merged into one skill and the redundant ones deleted. Manual via
        `/skills curate` (blocking, prints); also auto-run in the background after
        the learned library grows. Only ever touches trust:agent skills. Opt out
        with KORGEX_NO_CURATE=1; best-effort — never disturbs the session."""
        import os
        if os.environ.get("KORGEX_NO_CURATE", "").strip().lower() in ("1", "true", "yes"):
            if blocking:
                self._print("curation disabled (KORGEX_NO_CURATE)")
            return

        def _work():
            try:
                from src import skill_curator as _C
                from src import skill_usage as _SU
                from src import skills as _SK
                from src.pt_output import emit

                agent = self._ensure_agent()
                if agent is None:
                    if blocking:
                        self._print("connect a provider first — run `korgex setup`")
                    return

                def complete(system, user):
                    client = agent._get_client()
                    resp = agent._call(client, [{"role": "user", "content": user}], [],
                                       system_prompt=system)
                    return agent._extract_final_text(resp)

                reg = _SK.load_skills(_SK.default_skill_roots(self.repo_root))
                if len(_C.agent_skills(reg)) < 2:
                    if blocking:
                        self._print("nothing to curate — fewer than 2 learned skills")
                    return
                plan = _C.plan_curation(reg, _C.make_curator(complete))
                if not plan.groups:
                    if blocking:
                        self._print("✓ learned skills already tidy — nothing to merge")
                    return
                res = _C.apply_curation(plan, _SU.global_skills_dir(), reg)
                removed = len(res.get("removed", []))
                kept = ", ".join(res.get("merged", [])) or "(none)"
                msg = f"✦ curated skills: consolidated into {kept} · removed {removed} duplicate(s)"
                self._print(msg) if blocking else emit("\n" + msg + "\n")
            except Exception:
                if blocking:
                    self._print("curation failed (best-effort) — skills left unchanged")

        if blocking:
            _work()
        else:
            import threading
            threading.Thread(target=_work, daemon=True).start()

    def _do_rewind(self, arg):
        """/rewind — list undo points, or restore files to BEFORE a given prompt."""
        pts = self._rewind.points() if self._rewind else []
        if not pts:
            self._print("nothing to rewind — no file changes recorded this session yet")
            return
        if arg is None:
            self._print("rewind points (restore files to BEFORE the prompt):")
            for p in pts:
                preview = p.prompt if len(p.prompt) <= 60 else p.prompt[:59] + "…"
                self._print(f"  {p.turn}.  {preview}")
            self._print("→ /rewind <n> restores the files changed from prompt n onward")
            return
        try:
            target = int((arg or "").strip())
        except ValueError:
            self._print("usage: /rewind <prompt-number>  (run /rewind to list them)")
            return
        actions = self._rewind.restore(target)
        if not actions:
            self._print(f"no file changes recorded at or after prompt {target}")
            return
        self._rewind.forget_from(target)
        self._print(f"⟲ rewound to before prompt {target} — {len(actions)} file(s):")
        for path, act in actions:
            self._print(f"  {act}: {path}")
        self._print("(conversation kept — /clear to also reset the chat)")

    def _learn_from_turn(self, user_text: str, summary: str):
        """Background self-review: after a turn, ask the model (in a daemon thread,
        never blocking the REPL) whether a reusable skill emerged, and if so write it
        as an agent-owned skill. Best-effort — any failure is swallowed. Opt out with
        KORGEX_NO_LEARN=1."""
        import os
        if os.environ.get("KORGEX_NO_LEARN", "").strip().lower() in ("1", "true", "yes"):
            return
        agent = self._agent
        if agent is None:
            return

        def _bg():
            try:
                from src import skill_review as _SR
                from src import skill_usage as _SU
                from src import skills as _SK
                from src.pt_output import emit

                def complete(system, user):
                    client = agent._get_client()
                    resp = agent._call(client, [{"role": "user", "content": user}], [],
                                       system_prompt=system)
                    return agent._extract_final_text(resp)

                reg = _SK.load_skills(_SK.default_skill_roots(self.repo_root))
                verdict = _SR.review_turn(user_text, summary, reg.names(),
                                          reviewer=_SR.make_reviewer(complete))
                if verdict.action in ("create", "update"):
                    res = _SR.apply_verdict(verdict, _SU.global_skills_dir(), registry=reg)
                    if res.get("saved"):
                        emit(f"\n✦ learned skill: {res['name']}\n")
                        # The library just grew — if it's past the floor, consolidate
                        # near-duplicates in the background (new skills are rare, so
                        # this is naturally throttled to growth moments).
                        from src import skill_curator as _C
                        reg2 = _SK.load_skills(_SK.default_skill_roots(self.repo_root))
                        if len(_C.agent_skills(reg2)) >= self._CURATE_THRESHOLD:
                            self._curate_skills(blocking=False)
            except Exception:
                pass  # learning is an enhancement; never disturb the session

        import threading
        threading.Thread(target=_bg, daemon=True).start()

    def _toggle_plan(self, arg):
        """/plan [on|off] — turn plan mode on (read-only until you approve) or off.
        With no arg, toggles. 'approve' exits plan mode and lets execution proceed."""
        agent = self._ensure_agent()
        want = (arg or "").strip().lower()
        if want in ("approve", "go", "execute"):
            agent.approve_plan()
            self._print("✓ plan approved — executing (read-only lifted)")
            return
        if want == "on":
            agent.plan_mode_active = True
        elif want == "off":
            agent.plan_mode_active = False
        else:
            agent.plan_mode_active = not getattr(agent, "plan_mode_active", False)
        if agent.plan_mode_active:
            self._print("◐ plan mode ON — I'll stay read-only and propose a plan; "
                        "`/plan approve` to execute, `/plan off` to cancel")
        else:
            self._print("plan mode OFF")

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
                self._pick_model()        # interactive numbered, priced menu
            else:
                self._switch_model(cmd.arg)
            return True
        if cmd.kind == "plan":
            self._toggle_plan(cmd.arg)
            return True
        if cmd.kind == "skills":
            if (cmd.arg or "").strip().lower() == "curate":
                self._curate_skills(blocking=True)
            else:
                self._show_skills()
            return True
        if cmd.kind == "tasks":
            led = getattr(self._agent, "_task_ledger", None)
            if led is None or not led.tasks():
                self._print("no tasks yet — the agent creates a checklist when it plans multi-step work")
            else:
                self._print(f"tasks ({led.summary()}):")
                self._print(led.render())
            return True
        if cmd.kind == "jobs":
            from src.background_tasks import get_runner
            jobs = get_runner().all()
            if not jobs:
                self._print("no background jobs — the agent backgrounds long commands with Bash(background=true)")
            else:
                self._print("background jobs:")
                for j in jobs:
                    mark = {"running": "⏳", "done": "✓", "failed": "✗"}.get(j.status, "·")
                    self._print(f"  {mark} {j.id}  [{j.status}]  {j.command[:60]}")
            return True
        if cmd.kind == "rewind":
            self._do_rewind(cmd.arg)
            return True
        if cmd.kind == "unknown":
            self._print(f"unknown command: /{cmd.arg} — try /help")
            return True
        if cmd.kind == "turn":
            self._run_turn(cmd.arg)
            return True
        return True

    def _run_turn(self, text: str):
        """Stream one agent turn. The PromptSession has already exited (you pressed
        Enter, and your "› {text}" line is sitting in scrollback), so there is NO
        live app to fight: the spinner uses raw \\r to overwrite in place and the
        streamed reply prints directly to the terminal and stays put. (No
        patch_stdout — nothing useful mid-turn, and it would mangle the \\r spinner.)"""
        agent = self._ensure_agent()
        # @-file mentions: inline any @path the user referenced so "refactor
        # @src/a.py to use @src/b.py" just works. The model gets the file bodies;
        # the rewind label and skill-learning keep the ORIGINAL typed text.
        prompt = text
        try:
            from src import mentions as _MEN
            exp = _MEN.expand_mentions(text, cwd=self.repo_root)
            if exp["attached"]:
                self._print("· included " + ", ".join("@" + p for p in exp["attached"]))
                prompt = exp["text"]
        except Exception:
            pass
        # Track this prompt as a rewind point and snapshot start-of-turn file state.
        self._turn += 1
        if self._rewind is None:
            from src.rewind import RewindLog
            self._rewind = RewindLog()
        self._rewind.begin_turn(self._turn, text)
        _turn = self._turn
        agent._rewind_sink = lambda path, pre: self._rewind.record_pre(_turn, path, pre)
        try:
            result = agent.run_task(prompt)
            print()  # newline so the next turn's prompt starts clean
            summary = (result or {}).get("result", "") if isinstance(result, dict) else ""
            self._learn_from_turn(text, summary)  # background; never blocks
        except KeyboardInterrupt:
            self._print("\n(interrupted)")
        except Exception as e:  # never let one bad turn kill the session
            self._print(f"[error] {e}")

    def _banner(self, animated_portal: bool = False):
        """Paint the startup banner. With `animated_portal`, the wordmark still
        prints but the static welcome panel is skipped — the live app shows an
        animated portal splash instead (so we don't double the visual)."""
        import os
        from src import banner
        configured = self.cfg.is_configured() or bool(self.api_key)
        try:
            from src.cli import _get_version
            version = _get_version()
        except Exception:
            version = "dev"
        banner.render(model=self.model, cwd=os.getcwd(), version=version,
                      configured=configured, out=self.out)
        # The welcome panel (model/cwd/providers · MCP · skills · try-tips · summary)
        # ALWAYS shows. With `animated_portal`, just omit its static mascot column —
        # the live animated portal stands in for it, so the info + tips never vanish.
        if configured:
            try:
                from src import skills as _SK
                reg = _SK.load_skills(_SK.default_skill_roots(os.getcwd()))
                skills = [(n, reg.get(n).description) for n in reg.names()]
                providers = [p.get("type") for p in self.cfg.providers]
                try:
                    from src.tool_abstraction import get_tool_names
                    n_tools = len(get_tool_names())
                except Exception:
                    n_tools = 0
                banner.render_dashboard(model=self.model, cwd=os.getcwd(), version=version,
                                        providers=providers, skills=skills,
                                        mcps=self._mcp_names(), tools=n_tools,
                                        mascot=not animated_portal, out=self.out)
            except Exception:
                pass

    def _mcp_names(self) -> list:
        """MCP servers to show on the dashboard: CONNECTED ones if MCP is loaded
        (live truth), else the full CONFIGURED set across all sources (mcp.json +
        .mcp.json + .claude + .cursor + global) — matching `korgex mcp list`."""
        import os
        try:  # connected servers (after the agent loaded MCP) are the real truth
            from src.mcp_router import get_router
            connected = [s.get("server") for s in get_router().list_servers() if s.get("server")]
            if connected:
                return connected
        except Exception:
            pass
        try:  # otherwise show what's configured (all sources)
            from src import mcp_config
            return list(mcp_config.load_servers(cwd=os.getcwd()).keys())
        except Exception:
            return []

    def _mode_label(self) -> str:
        policy = (getattr(self._agent, "edit_policy", None) or "free")
        mode = "⚡ free" if policy in ("free", "session") else (
            "⚡ bypass" if policy == "bypass" else policy)
        plan = " · ◐ PLAN" if getattr(self._agent, "plan_mode_active", False) else ""
        return f"{mode}{plan}"

    def _bottom_toolbar(self):
        """Keybind / command hints, dim along the bottom (the status lives in the
        input's top border). Re-evaluated each render so it stays current."""
        return "  Enter send  ·  /help commands  ·  /plan  ·  /skills  ·  /rewind  ·  Ctrl-C quit  "

    def _prompt_message(self):
        """A framed prompt: a top border carrying the status, then the ›  caret.
        (A reliable inline frame — the full bordered box is the full-screen TUI,
        which previously hid streamed replies.)"""
        import shutil

        from prompt_toolkit.formatted_text import FormattedText
        width = shutil.get_terminal_size((80, 24)).columns
        label = f" korgex · {self.model} · {self._mode_label()} "
        fill = max(0, width - len(label) - 3)
        top = "╭─" + label + "─" * fill + "╮"
        return FormattedText([("class:frame", top + "\n"), ("class:caret", "› ")])

    def _prompt_style(self):
        from prompt_toolkit.styles import Style
        return Style.from_dict({
            "frame": "#46525f",                 # dim border
            "caret": "#a5de67 bold",            # green caret
            "bottom-toolbar": "#6b7480 noreverse",  # dim hints, not a reversed bar
        })

    def _session(self):
        """Lazily build the prompt_toolkit session: bottom-anchored input with
        in-memory history. Cached so history persists across turns."""
        if self._session_obj is None:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory
            self._session_obj = PromptSession(history=InMemoryHistory())
        return self._session_obj

    def _read_line(self) -> str:
        """Read one line via the prompt_toolkit session: a framed prompt (top border
        + ›  caret) with a dim hint bar beneath. Raises EOFError/KeyboardInterrupt to
        end the loop (caught in run())."""
        return self._session().prompt(self._prompt_message,
                                      bottom_toolbar=self._bottom_toolbar,
                                      style=self._prompt_style())

    def run(self):
        """Start the REPL.

        Input is driven by a transient PromptSession, NOT a persistent full-screen
        Application — and that choice is about correctness, not simplicity. A
        PromptSession's prompt() exits the instant you press Enter, so while a turn
        streams there is NO application rendering: the reply prints straight to the
        terminal and stays in scrollback. The earlier Application kept an app alive
        across the whole turn, and its renderer swallowed/clobbered the streamed
        reply (it came out invisible). The robust rule: when no app
        is running, a direct print is the safe path; it only keeps a live app by
        running the agent on a background thread and marshalling every line back to
        the UI thread via run_in_terminal+patch_stdout. Until we build that
        machinery, the transient prompt is the reliable, legible path.
        """
        self._run_simple()

    def _sweep_skills(self):
        """Age agent-learned skills by idle time on startup (active→stale→archived,
        never deleted; only agent-owned skills). Cheap, pure, best-effort."""
        try:
            import time

            from src import skill_usage as _SU
            from src import skills as _SK
            reg = _SK.load_skills(_SK.default_skill_roots(self.repo_root))
            store = _SU.UsageStore(_SU.usage_path(_SU.global_skills_dir()))
            _SU.sweep(store, reg, now=time.time())
        except Exception:
            pass

    def _run_simple(self):
        """Fallback loop: inline PromptSession (input wherever the cursor is)."""
        # Connect MCP servers BEFORE the banner so the dashboard shows the real
        # connected servers + their tools (and they're ready for turn 1). Skipped
        # entirely when nothing is configured, so startup stays instant.
        if self._mcp_configured():
            self._print("· connecting MCP server(s)…")
            try:
                self._ensure_agent()
            except Exception:
                pass
        self._banner()
        self._sweep_skills()
        while True:
            try:
                line = self._read_line()
            except (EOFError, KeyboardInterrupt):
                self._print("")
                break
            if not self.handle(parse_repl_input(line)):
                break
