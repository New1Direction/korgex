"""
korgantic.py — korgex's max-power mode: an effort dial over workflow chaining.

Six effort levels scale how much korgex does:

    auto → low → medium → high → xhigh → ultracode

- low      : single implement pass, tight budget.
- medium   : implement + a single-skeptic review.
- high     : design → implement → review (2-skeptic verify) + completeness critic.
- xhigh    : understand(sweep) → design → implement → review(3-skeptic) + critic + 1 dry-loop.
- ultracode: the same full chain, 3-skeptic verify, completeness critic, loop-until-dry,
             and **token cost is not a constraint** (unbounded budget).

The chain is understand → design → implement → review, and the quality patterns
are: multi-modal sweep (understand), loop-until-dry (implement), adversarial
verify (review), completeness critic (final).

Everything here is pure orchestration over an injected `runner(role, prompt,
output_schema=None) -> {"success", "result", ...}`. In production the runner
spawns each phase as a subagent chained under one korgantic root, so a full
korgantic run is a single causal DAG in the ledger — rewindable per phase.
"""

from __future__ import annotations

EFFORT_LEVELS = ["auto", "low", "medium", "high", "xhigh", "ultracode"]

# Per-level behavior. phase_count and verifiers are deliberately non-decreasing.
_PROFILES = {
    "low": {
        "phases": ["implement"], "verifiers": 0, "verify_quorum": 1,
        "sweep": False, "critic": False, "loop_dry": 0,
        "max_iter": 15, "token_budget": 50_000,
    },
    "medium": {
        "phases": ["implement", "review"], "verifiers": 1, "verify_quorum": 1,
        "sweep": False, "critic": False, "loop_dry": 0,
        "max_iter": 25, "token_budget": 150_000,
    },
    "high": {
        "phases": ["design", "implement", "review"], "verifiers": 2, "verify_quorum": 2,
        "sweep": False, "critic": True, "loop_dry": 0,
        "max_iter": 40, "token_budget": 400_000,
    },
    "xhigh": {
        "phases": ["understand", "design", "implement", "review"], "verifiers": 3, "verify_quorum": 2,
        "sweep": True, "critic": True, "loop_dry": 1,
        "max_iter": 60, "token_budget": 1_000_000,
    },
    "ultracode": {
        "phases": ["understand", "design", "implement", "review"], "verifiers": 3, "verify_quorum": 2,
        "sweep": True, "critic": True, "loop_dry": 2,
        "max_iter": 100, "token_budget": None,  # "token cost is not a constraint"
    },
}

# Structured-output schemas the review/verify/critic phases ask their agents for.
FINDINGS_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {"findings": {"type": "array", "items": {"type": "object"}}},
    "required": ["findings"],
}
VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {"refuted": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["refuted"],
}
MISSING_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {"missing": {"type": "array", "items": {"type": "string"}}},
    "required": ["missing"],
}

_SWEEP_LENSES = ["structure", "dependencies", "tests", "risks"]

_HEAVY = ("comprehensive", "comprehensively", "thorough", "exhaustive", "audit",
          "entire", "whole codebase", "production-grade", "redesign", "migrate")


def _auto_level(task: str) -> str:
    """Heuristic for auto: short/simple → low, heavy keywords → high, else medium."""
    t = (task or "").lower()
    if any(k in t for k in _HEAVY):
        return "high"
    if len(t) < 80 and not any(k in t for k in ("refactor", "implement", "migrate", "redesign", "build ")):
        return "low"
    return "medium"


def resolve_effort(name: str, task: str = "") -> tuple:
    """Resolve an effort name (incl. 'auto') to a concrete (level, profile-copy)."""
    key = (name or "auto").strip().lower()
    if key == "auto":
        key = _auto_level(task)
    if key not in _PROFILES:
        key = "medium"
    return key, dict(_PROFILES[key])


# ── quality patterns (pure, over an injected runner) ──────────────────────

def adversarial_verify(claim, runner, n: int = 3, quorum: int = 2) -> dict:
    """Spawn n skeptics, each prompted to REFUTE the claim (default refuted if unsure).

    The claim survives only if at least `quorum` skeptics fail to refute it.
    """
    votes = []  # list of `refuted` booleans
    for _ in range(max(1, n)):
        r = runner(
            "verify",
            f"Try to REFUTE this finding. Default refuted=true if you are unsure. Finding: {claim}",
            output_schema=VERDICT_SCHEMA,
        )
        obj = (r or {}).get("result") or {}
        votes.append(bool(obj.get("refuted", True)))
    not_refuted = sum(1 for v in votes if not v)
    return {"confirmed": not_refuted >= quorum, "votes": votes,
            "n": n, "quorum": quorum, "not_refuted": not_refuted}


def loop_until_dry(round_fn, dry_threshold: int = 2, max_rounds: int = 10) -> list:
    """Call round_fn() until it returns an empty list `dry_threshold` times in a row.

    round_fn returns the list of new items found this round ([] == dry). Returns
    the per-round result lists. Caps at max_rounds so it always terminates.
    """
    results = []
    dry = 0
    for _ in range(max(1, max_rounds)):
        out = round_fn() or []
        results.append(out)
        if out:
            dry = 0
        else:
            dry += 1
            if dry >= dry_threshold:
                break
    return results


def parallel(thunks, max_workers: int = 8) -> list:
    """Run zero-arg thunks concurrently and gather results in submission order.

    A barrier: returns once all complete. Per-thunk error isolation — a thunk
    that raises resolves to None rather than failing the batch. Safe to fan out
    agents IFF they write through a ThreadSafeLedger (see korg_ledger).
    """
    from concurrent.futures import ThreadPoolExecutor

    thunks = list(thunks)
    if not thunks:
        return []
    results = [None] * len(thunks)
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(thunks)))) as ex:
        futures = {ex.submit(th): i for i, th in enumerate(thunks)}
        for fut in futures:
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = None  # error isolation: one bad agent ≠ a failed batch
    return results


def multi_modal_sweep(lenses, runner, base_prompt: str) -> list:
    """Run one understand-agent per lens CONCURRENTLY — each blind to the others.

    Lenses are independent, so this is a genuine fan-out. `l=lens` binds the loop
    var per-thunk (avoids late-binding closure capture).
    """
    thunks = [
        (lambda l=lens: runner("understand", f"[{l} lens] Analyze for this task: {base_prompt}"))
        for lens in lenses
    ]
    return parallel(thunks)


def completeness_critic(task, runner) -> list:
    """Ask a final critic what's MISSING. Returns the list of gaps."""
    r = runner(
        "critic",
        f"What is MISSING from the work on '{task}'? List concrete gaps: unhandled "
        f"cases, untested paths, unverified claims, modalities not run.",
        output_schema=MISSING_SCHEMA,
    )
    return ((r or {}).get("result") or {}).get("missing", []) or []


# ── the controller ────────────────────────────────────────────────────────

def _implement_round(runner, task: str) -> list:
    r = runner("implement", f"Implement (incrementally; report remaining changes): {task}")
    res = (r or {}).get("result") or {}
    return res.get("changes", []) if isinstance(res, dict) else []


def _finding_text(f) -> str:
    if isinstance(f, dict):
        return f.get("title") or f.get("desc") or str(f)
    return str(f)


def run_korgantic(task: str, effort: str, runner) -> dict:
    """Run the effort-scaled workflow chain. `runner` executes one phase."""
    level, prof = resolve_effort(effort, task)
    phases_run = []
    artifacts = {}

    if "understand" in prof["phases"]:
        phases_run.append("understand")
        if prof["sweep"]:
            artifacts["understand"] = multi_modal_sweep(_SWEEP_LENSES, runner, task)
        else:
            artifacts["understand"] = [runner("understand", f"Understand the context for: {task}")]

    if "design" in prof["phases"]:
        phases_run.append("design")
        artifacts["design"] = runner("design", f"Design an approach for: {task}")

    # implement always runs.
    phases_run.append("implement")
    if prof["loop_dry"]:
        artifacts["implement"] = loop_until_dry(
            lambda: _implement_round(runner, task),
            dry_threshold=prof["loop_dry"], max_rounds=max(2, prof["loop_dry"] + 3),
        )
    else:
        artifacts["implement"] = [runner("implement", f"Implement: {task}")]

    findings = []
    if "review" in prof["phases"]:
        phases_run.append("review")
        review = runner("review", f"Review the implementation of: {task}",
                        output_schema=FINDINGS_SCHEMA)
        raw = ((review or {}).get("result") or {}).get("findings", []) or []
        for f in raw:
            if prof["verifiers"]:
                verdict = adversarial_verify(_finding_text(f), runner,
                                             prof["verifiers"], prof["verify_quorum"])
                if verdict["confirmed"]:
                    findings.append({**f, "verdict": verdict})
            else:
                findings.append(f)

    if prof["critic"]:
        phases_run.append("completeness")
        artifacts["completeness"] = completeness_critic(task, runner)

    return {
        "effort": level,
        "phases_run": phases_run,
        "findings": findings,
        "artifacts": artifacts,
        "token_budget": prof["token_budget"],
    }
