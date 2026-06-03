"""Session resume — replay the verifiable journal back into context.

korgex records every turn to the korg-ledger: the user's prompt (`user_prompt`), the
model's reply text (`llm_inference` → `result.text`), and every tool call + result. Resume
reconstructs a readable transcript of a prior session from that record and feeds it back as
context, so you pick up where you left off. Because it's rebuilt from the tamper-evident
ledger, the thing you resume from is itself verifiable — `korgex verify` still passes.

A session is delimited by a lightweight `session_start` marker (just a conventional ledger
event — no schema change). Journals written before markers existed fall back to "the last N
turns". This is the v1 "transcript-as-context" approach: simple, provider-agnostic, robust.
"""
from __future__ import annotations

import time
import uuid

from src.korg_ledger import load_journal_raw

SESSION_START = "session_start"
_TRIM_MARKER = "[... earlier turns trimmed ...]"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _events(journal_path: str) -> list:
    try:
        return load_journal_raw(journal_path)
    except (OSError, ValueError):
        return []


def mark_session_start(korg, *, cwd: str, model: str) -> str | None:
    """Record a session boundary marker and return its id (``sess_<8hex>``), or None if
    there's no ledger. It's an ordinary tool-call event, so `verify` treats it like any
    other chained event — no ledger-spec change. The id is prefixed (not bare hex) so the
    ledger's secret-redaction never mistakes it for a credential."""
    if korg is None:
        return None
    sid = "sess_" + uuid.uuid4().hex[:8]
    try:
        korg.record_tool_call(
            SESSION_START,
            {"session_id": sid, "cwd": cwd, "model": model, "started_at": _now_iso()},
            {}, True, 0,
        )
    except Exception:
        return None
    return sid


# ── transcript rendering ──────────────────────────────────────────────────────

def _s(v, limit: int = 400) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        if isinstance(v, dict) and any(k in v for k in ("ref", "sha256", "content_ref")):
            return "[large content — sealed to a content-ref]"
        v = str(v)
    v = " ".join(v.split())
    return v if len(v) <= limit else v[:limit] + "…"


def _short_args(args, limit: int = 90) -> str:
    if not isinstance(args, dict):
        return _s(args, limit)
    s = ", ".join(f"{k}={_s(v, 40)}" for k, v in args.items())
    return s if len(s) <= limit else s[:limit] + "…"


def _short_result(res, limit: int = 90) -> str:
    if isinstance(res, dict):
        for k in ("error", "stdout", "content", "result", "ok", "exit_code", "count"):
            if k in res:
                return _s(res[k], limit)
        return _s(res, limit)
    return _s(res, limit)


def _render_transcript(events: list, *, max_chars: int) -> str:
    lines = []
    for ev in events:
        tn = ev.get("tool_name")
        if tn == SESSION_START:
            continue
        args = ev.get("args") or {}
        res = ev.get("result") or {}
        if tn == "user_prompt":
            lines.append(f"▸ you: {_s(args.get('prompt'))}")
        elif tn == "llm_inference":
            txt = res.get("text")
            if txt:
                lines.append(f"  korgex: {_s(txt, 600)}")
        else:
            lines.append(f"  · {tn}({_short_args(args)}) → {_short_result(res)}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = _TRIM_MARKER + "\n" + text[-max_chars:]   # keep the MOST RECENT turns
    return text


def _last_n_turns(events: list, n: int) -> list:
    """Fallback for journals with no session markers: events from the start of the Nth-from-last
    user prompt to the end (so we replay whole recent turns, not a sliced one)."""
    idxs = [i for i, e in enumerate(events) if e.get("tool_name") == "user_prompt"]
    if not idxs:
        return list(events)
    start = idxs[-n] if len(idxs) >= n else idxs[0]
    return events[start:]


# ── public API ──────────────────────────────────────────────────────────────

def list_sessions(journal_path: str) -> list[dict]:
    """Every session in the journal, oldest→newest."""
    events = _events(journal_path)
    starts = [(i, ev) for i, ev in enumerate(events) if ev.get("tool_name") == SESSION_START]
    out = []
    for k, (i, ev) in enumerate(starts):
        a = ev.get("args") or {}
        end = starts[k + 1][0] if k + 1 < len(starts) else len(events)
        prompts = [e for e in events[i + 1:end] if e.get("tool_name") == "user_prompt"]
        out.append({
            "session_id": a.get("session_id"),
            "cwd": a.get("cwd"),
            "model": a.get("model"),
            "started_at": a.get("started_at"),
            "seq": ev.get("seq_id"),
            "turns": len(prompts),
            "first_prompt": _s((prompts[0].get("args") or {}).get("prompt")) if prompts else None,
        })
    return out


def build_resume_context(journal_path: str, session_id: str | None = None, *,
                         max_chars: int = 12000, fallback_turns: int = 12) -> dict:
    """Rebuild a transcript for one session. Defaults to the LAST session; pass `session_id`
    to target a specific one. Returns {found, session_id, meta, turns, transcript}."""
    empty = {"found": False, "session_id": None, "meta": {}, "turns": 0, "transcript": ""}
    events = _events(journal_path)
    if not events:
        return empty
    starts = [(i, ev) for i, ev in enumerate(events) if ev.get("tool_name") == SESSION_START]

    if not starts:
        span, sid, meta = _last_n_turns(events, fallback_turns), None, {}
    else:
        if session_id is not None:
            chosen = next(((i, ev) for i, ev in starts
                           if (ev.get("args") or {}).get("session_id") == session_id), None)
            if chosen is None:
                return empty
        else:
            chosen = starts[-1]
        ci, cev = chosen
        later = [i for i, _ in starts if i > ci]
        end = later[0] if later else len(events)
        span = events[ci + 1:end]
        meta = cev.get("args") or {}
        sid = meta.get("session_id")

    transcript = _render_transcript(span, max_chars=max_chars)
    turns = sum(1 for e in span if e.get("tool_name") == "user_prompt")
    return {"found": bool(transcript.strip()), "session_id": sid,
            "meta": meta, "turns": turns, "transcript": transcript}


def resume_preamble(ctx: dict) -> str:
    """Frame a reconstructed transcript as a context preamble for the model."""
    sid = ctx.get("session_id") or "the previous session"
    where = (ctx.get("meta") or {}).get("cwd")
    loc = f" in {where}" if where else ""
    return (
        f"[RESUMING SESSION {sid}] You are continuing a prior korgex session{loc} "
        f"({ctx.get('turns', 0)} turns), rebuilt from the verifiable korg-ledger. "
        f"Transcript so far:\n\n{ctx.get('transcript', '')}\n\n"
        f"[END TRANSCRIPT] Continue from here — the user's next message follows."
    )
