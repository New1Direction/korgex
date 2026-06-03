"""Cognition trace — make the verifiable ledger legible.

korgex records its own cognition to a hash-chained, tamper-evident ledger: every
event carries a ``seq_id`` and a ``triggered_by`` (the seq that caused it), forming
a causal DAG — a user_prompt at the root, the llm_inference rounds it spawned, and
the tool_calls each round requested. This reconstructs that DAG and renders it as a
readable tree: *what the agent did, and what caused it.*

It's trustworthy precisely because the underlying chain is verifiable — `korgex
verify` proves the journal wasn't edited, so the trace can't be quietly faked. That
pairing (explainable + verifiable cognition) is the thing the closed agents can't
offer. Pure: events in, tree/text out.
"""
from __future__ import annotations

_GREEN = "\033[32m"
_RED = "\033[31m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def _seq(e):
    return e.get("seq_id", e.get("seq"))


def _kind(e):
    return e.get("tool_name") or e.get("event_type") or e.get("kind") or "?"


def build_forest(events) -> list:
    """Reconstruct the causal forest. Each node is the event dict plus a
    ``children`` list (events whose ``triggered_by`` is this event's seq). Roots are
    events with no parent, or whose parent isn't present (orphans surface as roots
    rather than being lost). Stable order: by seq_id.
    """
    nodes = {}
    order = []
    for e in events or []:
        s = _seq(e)
        if s is None:
            continue
        node = dict(e)
        node["children"] = []
        nodes[s] = node
        order.append(s)

    roots = []
    for s in order:
        node = nodes[s]
        parent = node.get("triggered_by")
        if parent is not None and parent in nodes and parent != s:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)          # true root, or orphan (parent absent)
    return roots


def _label(node, color: bool) -> str:
    kind = _kind(node)
    args = node.get("args") or {}
    if kind == "user_prompt":
        prompt = (args.get("prompt") or node.get("prompt") or "").strip().replace("\n", " ")
        if len(prompt) > 80:
            prompt = prompt[:79] + "…"
        text = f"▎ prompt: {prompt}"
        return f"{_CYAN}{text}{_RESET}" if color else text
    if kind == "llm_inference":
        model = args.get("model", "?")
        text = f"· thought ({model})"
        return f"{_DIM}{text}{_RESET}" if color else text
    # a real tool call
    target = args.get("file_path") or args.get("command") or args.get("path") or ""
    if len(str(target)) > 60:
        target = str(target)[:59] + "…"
    ok = node.get("success", True)
    mark = "✓" if ok else "✗"
    ms = node.get("duration_ms")
    timing = f" {ms}ms" if isinstance(ms, int) else ""
    body = f"{mark} {kind} {target}".rstrip() + timing
    if not color:
        return body
    return f"{(_GREEN if ok else _RED)}{mark}{_RESET} {kind} {target}".rstrip() + f"{_DIM}{timing}{_RESET}"


def _render_node(node, depth, lines, color):
    lines.append(("  " * depth) + _label(node, color))
    for child in node.get("children", []):
        _render_node(child, depth + 1, lines, color)


def render_roots(roots, *, color: bool = True) -> str:
    """Render specific root nodes (from build_forest) as indented trees. Lets a
    caller show just the latest request instead of the whole journal."""
    if not roots:
        return ""
    lines = []
    for root in roots:
        _render_node(root, 0, lines, color)
    return "\n".join(lines)


def render_trace(events, *, color: bool = True) -> str:
    """Render the whole causal forest as an indented cognition tree. "" when empty."""
    return render_roots(build_forest(events), color=color)


# ── Targeted "why": trace a file change back to its cause ────────────────────

def causal_path(by_seq: dict, seq) -> list:
    """Walk ``triggered_by`` from `seq` back to the root, returned root→seq (forward
    order). `by_seq` maps seq_id → event. Cycle-safe."""
    path, cur, seen = [], seq, set()
    while cur is not None and cur in by_seq and cur not in seen:
        seen.add(cur)
        e = by_seq[cur]
        path.append(e)
        cur = e.get("triggered_by")
    path.reverse()
    return path


def _touches(e, target: str) -> bool:
    args = e.get("args") or {}
    # `name` lets `why <skill>` match a skill.learned/curated or a Skill invocation —
    # so a learned skill's provenance traces back to its prompt like any file edit.
    hay = " ".join(str(args.get(k, ""))
                   for k in ("file_path", "path", "notebook_path", "command", "name"))
    return bool(target) and target in hay


def explain_why(events, target: str, *, color: bool = True) -> str:
    """Explain why `target` (a file path, command, or skill name) was touched: find
    every event that acted on it and render the causal chain from the originating
    user_prompt down to each touch. Trustworthy because the chain is verifiable
    (`korgex verify`)."""
    by_seq = {_seq(e): e for e in (events or []) if _seq(e) is not None}
    touches = [e for e in (events or [])
               if _kind(e) not in ("user_prompt", "llm_inference") and _touches(e, target)]
    if not touches:
        return f"no recorded action touched {target}"

    out = [f"why {target}? — touched {len(touches)}×, each traced to the prompt that caused it:", ""]
    for t in touches:
        path = causal_path(by_seq, _seq(t))
        root = path[0] if path else t
        rargs = root.get("args") or {}
        prompt = (rargs.get("prompt") or root.get("prompt") or _kind(root)).strip().replace("\n", " ")
        if len(prompt) > 56:
            prompt = prompt[:55] + "…"
        cause = f'← "{prompt}"  (#{_seq(root)})'
        cause = f"{_DIM}{cause}{_RESET}" if color else cause
        out.append(f"  {_label(t, color)}   {cause}")
    return "\n".join(out)
