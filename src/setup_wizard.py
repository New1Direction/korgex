"""korgex setup — connect any model provider, no lock-in.

`build_config` and `suggest_default_model` are pure and tested; `run_setup` is the
interactive getpass/input shell over them. Cloud providers (openrouter/anthropic/
openai) take an API key; local (ollama) takes a base_url and no key.
"""
from __future__ import annotations

import sys

from src import config as C

PROVIDER_TYPES = ["openrouter", "anthropic", "openai", "ollama"]

_SUGGEST = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-opus-4-8",
    "ollama": "llama3.3",
}

_BLURB = {
    "openrouter": "one key → hundreds of models across every major lab",
    "anthropic": "Claude models, direct",
    "openai": "GPT / o-series, direct",
    "ollama": "local models on your machine — no key needed",
}


def suggest_default_model(ptype: str) -> str:
    """A sensible default model id for a provider type (a suggestion, not a limit)."""
    return _SUGGEST.get(ptype, "claude-sonnet-4-6")


def build_config(answers: list[dict], default_model: str | None) -> C.Config:
    """Pure: assemble a Config from answered provider prompts.

    Each answer is ``{"type", "api_key"?}`` for a cloud provider or
    ``{"type": "ollama", "base_url"}`` for local. Order is preserved.
    """
    providers: list[dict] = []
    for a in answers:
        t = a.get("type")
        if not t:
            continue
        if t == "ollama":
            providers.append({"type": t, "base_url": a.get("base_url", "http://localhost:11434")})
        else:
            entry = {"type": t}
            if a.get("api_key"):
                entry["api_key"] = a["api_key"]
            providers.append(entry)
    return C.Config(default_model=default_model, providers=providers)


# ── interactive shell (thin over the pure core above) ─────────────────────────

def run_setup(path: str | None = None, _input=input, _getpass=None, out=None) -> int:
    """Interactive wizard. Connect one or more providers, pick a default model,
    save to ~/.korgex/config.json (0o600). Returns a shell exit code.

    `_input`/`_getpass`/`out` are injectable for testing the flow; in normal use
    they default to real stdin/getpass/stdout.
    """
    import getpass as _gp
    getpass_fn = _getpass or _gp.getpass
    out = out or sys.stdout

    def say(*a):
        print(*a, file=out)

    existing = C.load_config(path)
    say("korgex setup — connect any model provider (no lock-in).")
    if existing.is_configured():
        connected = ", ".join(p.get("type", "?") for p in existing.providers)
        say(f"already connected: {connected}  (this will replace it)")
    say("")

    answers: list[dict] = []
    while True:
        say("providers:")
        for i, t in enumerate(PROVIDER_TYPES, 1):
            say(f"  {i}. {t} — {_BLURB.get(t, '')}")
        choice = _input("pick a provider (number, or blank to finish): ").strip()
        if not choice:
            break
        try:
            ptype = PROVIDER_TYPES[int(choice) - 1]
        except (ValueError, IndexError):
            say("  (not a valid choice)")
            continue

        if ptype == "ollama":
            url = _input("  ollama base url [http://localhost:11434]: ").strip() or "http://localhost:11434"
            answers.append({"type": ptype, "base_url": url})
            say(f"  ✓ {ptype} ({url})")
        else:
            key = getpass_fn(f"  {ptype} API key (hidden): ").strip()
            if not key:
                say("  (no key entered — skipped)")
                continue
            answers.append({"type": ptype, "api_key": key})
            say(f"  ✓ {ptype} connected")

        if _input("add another provider? [y/N]: ").strip().lower() not in ("y", "yes"):
            break
        say("")

    if not answers:
        say("\nnothing connected — run `korgex setup` again when ready.")
        return 1

    first = answers[0]["type"]
    suggested = suggest_default_model(first)
    chosen = _input(f"\ndefault model [{suggested}]: ").strip() or suggested

    cfg = build_config(answers, default_model=chosen)
    saved = C.save_config(cfg, path)
    say(f"\n✓ saved to {saved}")
    say(f"  default model: {chosen}")
    say("\nrun `korgex` to start a session, or `korgex \"a task\"` for one-shot.")
    return 0
