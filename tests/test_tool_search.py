"""Tiered tool exposure + a real ToolSearch index.

Today ToolSearch is a no-op and every tool's full schema is sent to the model
every turn — which balloons tokens and degrades attention as MCP/plugin tools
accumulate. These tests pin: (1) a pure lexical ranker over name+description, and
(2) the exposure tiers (direct = always sent, deferred = found via ToolSearch).
"""
from src import tool_search as TS


# ── the pure ranker ──────────────────────────────────────────────────────────

def _docs():
    return [
        {"name": "Read", "description": "read a file from the local filesystem"},
        {"name": "Write", "description": "create or overwrite a file with new content"},
        {"name": "GrepCode", "description": "search file contents with a regular expression"},
        {"name": "RunTests", "description": "run the project test suite and report failures"},
        {"name": "SlackPost", "description": "send a message to a slack channel"},
    ]


def test_rank_finds_the_obviously_relevant_tool_first():
    hits = TS.rank("run the tests", _docs(), limit=3)
    assert hits[0]["name"] == "RunTests"


def test_rank_matches_on_description_not_just_name():
    # "regular expression" appears only in GrepCode's description.
    hits = TS.rank("regular expression search", _docs(), limit=2)
    assert hits[0]["name"] == "GrepCode"


def test_rank_respects_limit():
    hits = TS.rank("file", _docs(), limit=2)
    assert len(hits) <= 2


def test_rank_no_match_returns_empty():
    hits = TS.rank("xyzzy nonsense plugh", _docs(), limit=5)
    assert hits == []


def test_rank_is_case_insensitive():
    hits = TS.rank("SLACK", _docs(), limit=1)
    assert hits and hits[0]["name"] == "SlackPost"


# ── the exposure-tier decision ─────────────────────────────────────────────────

def test_below_threshold_everything_is_direct():
    # A handful of tools → no need to defer; all stay directly visible.
    decided = TS.assign_exposure(["a", "b", "c"], deferred_names=set(), threshold=100)
    assert all(v == "direct" for v in decided.values())


def test_at_or_above_threshold_deferrable_tools_flip_to_deferred():
    names = [f"t{i}" for i in range(120)]
    deferred = set(names[10:])  # the MCP/plugin tools, say
    decided = TS.assign_exposure(names, deferred_names=deferred, threshold=100)
    assert decided["t0"] == "direct"          # core tool, never deferred
    assert decided["t50"] == "deferred"        # deferrable + over threshold
    assert sum(1 for v in decided.values() if v == "deferred") == len(deferred)


def test_core_tools_never_deferred_even_over_threshold():
    names = [f"t{i}" for i in range(200)]
    # deferred_names is empty → nothing is eligible to defer, so all direct
    decided = TS.assign_exposure(names, deferred_names=set(), threshold=100)
    assert all(v == "direct" for v in decided.values())


# ── registry wiring (integration) ──────────────────────────────────────────────

def test_visible_excludes_deferred_until_searched(monkeypatch):
    import src.tool_abstraction as TA

    # Build an isolated registry: a few direct tools + many deferred ones so the
    # threshold trips. (Save/restore the module globals.)
    saved, saved_staged = dict(TA.USER_TOOLS), set(TA._STAGED_TOOLS)
    saved_mcp = set(TA._MCP_TOOLS)
    try:
        TA.USER_TOOLS.clear(); TA._STAGED_TOOLS.clear(); TA._MCP_TOOLS.clear()
        monkeypatch.setattr(TA, "DEFER_THRESHOLD", 3)
        TA.register_user_tool("Read", "read a file", [], exposure="direct")
        TA.register_user_tool("Write", "write a file", [], exposure="direct")
        TA.register_user_tool("SlackPost", "send a message to a slack channel", [], exposure="deferred")
        TA.register_user_tool("JiraTicket", "create a jira ticket", [], exposure="deferred")

        vis = set(TA.visible_tool_names())
        assert "Read" in vis and "Write" in vis           # direct: always visible
        assert "SlackPost" not in vis and "JiraTicket" not in vis  # deferred: hidden until searched

        # The model searches; the slack tool gets staged and becomes visible.
        res = TA.tool_search("post a slack message", limit=2)
        assert any(m["name"] == "SlackPost" for m in res["matches"])
        vis2 = set(TA.visible_tool_names())
        assert "SlackPost" in vis2
    finally:
        TA.USER_TOOLS.clear(); TA.USER_TOOLS.update(saved)
        TA._STAGED_TOOLS.clear(); TA._STAGED_TOOLS.update(saved_staged)
        TA._MCP_TOOLS.clear(); TA._MCP_TOOLS.update(saved_mcp)


def test_toolsearch_dispatch_routes_to_index():
    import src.tool_abstraction as TA
    # ToolSearch must route to the meta-handler, not "Unknown tool".
    out = TA.route_tool_call("ToolSearch", {"query": "read a file"})
    assert "matches" in out and "query" in out


def test_below_threshold_deferred_still_shown():
    """With a small registry, deferral is OFF — even deferred tools stay visible."""
    import src.tool_abstraction as TA
    saved, saved_staged = dict(TA.USER_TOOLS), set(TA._STAGED_TOOLS)
    try:
        TA.USER_TOOLS.clear(); TA._STAGED_TOOLS.clear()
        TA.register_user_tool("Read", "read", [], exposure="direct")
        TA.register_user_tool("SlackPost", "slack", [], exposure="deferred")
        # threshold default (60) >> 2 tools → deferral disabled
        assert "SlackPost" in set(TA.visible_tool_names())
    finally:
        TA.USER_TOOLS.clear(); TA.USER_TOOLS.update(saved)
        TA._STAGED_TOOLS.clear(); TA._STAGED_TOOLS.update(saved_staged)


# ── end-to-end: ToolSearch staged tool appears on the NEXT loop turn ───────────

class _FakeLedger:
    def __init__(self): self.n = 0
    def record_tool_call(self, **kw): self.n += 1; return self.n
    def record_user_prompt(self, prompt, triggered_by=None): self.n += 1; return self.n
    def record_llm_call(self, **kw): self.n += 1; return self.n


def test_loop_refreshes_tools_after_toolsearch(monkeypatch, tmp_path):
    """The real bug: tools_payload is built once before the loop. After the model
    calls ToolSearch, the staged deferred tool must appear in the payload on the
    NEXT round-trip — not stay hidden because the payload was frozen."""
    import src.tool_abstraction as TA
    from src.agent import KorgexAgent

    saved, saved_staged = dict(TA.USER_TOOLS), set(TA._STAGED_TOOLS)
    try:
        TA.USER_TOOLS.clear(); TA._STAGED_TOOLS.clear()
        monkeypatch.setattr(TA, "DEFER_THRESHOLD", 2)
        TA.register_user_tool("Read", "read a file", [], exposure="direct")
        TA.register_user_tool("DeployRocket", "deploy the rocket to orbit", [], exposure="deferred")

        seen_tool_names = []  # tool-name sets the fake client is offered, per turn

        agent = KorgexAgent(model="claude-sonnet-4-6", interactive=False,
                            repo_root=str(tmp_path), ledger=_FakeLedger())

        # Turn 1: model calls ToolSearch("deploy"). Turn 2: it should now SEE
        # DeployRocket in its offered tools and call it. Turn 3: plain finish.
        turns = iter([
            [{"id": "c1", "name": "ToolSearch", "args": {"query": "deploy rocket"}}],
            [{"id": "c2", "name": "DeployRocket", "args": {}}],
            [],
        ])

        def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
            seen_tool_names.append({t["name"] for t in tools_payload})
            class R: pass
            r = R(); r._calls = next(turns); r.usage = None
            return r

        monkeypatch.setattr(agent, "_get_client", lambda: object())
        monkeypatch.setattr(agent, "_call", fake_call)
        monkeypatch.setattr(agent, "_extract_tool_calls", lambda r: r._calls)
        monkeypatch.setattr(agent, "_extract_final_text", lambda r: "")
        monkeypatch.setattr(agent, "_assistant_turn", lambda r: {"role": "assistant", "content": ""})
        monkeypatch.setattr(agent, "_tool_result_turn", lambda cid, res: {"role": "user", "content": "ok"})
        monkeypatch.setattr(agent, "_dispatch_call",
                            lambda call, seq, tf=None: TA.route_tool_call(call["name"], call["args"]))

        agent.run_task("get the rocket up")

        # Turn 1: DeployRocket hidden (deferred, unsearched). Turn 2: now visible.
        assert "DeployRocket" not in seen_tool_names[0], "deferred tool should start hidden"
        assert "DeployRocket" in seen_tool_names[1], "staged tool must appear next turn"
    finally:
        TA.USER_TOOLS.clear(); TA.USER_TOOLS.update(saved)
        TA._STAGED_TOOLS.clear(); TA._STAGED_TOOLS.update(saved_staged)
