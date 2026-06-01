"""Model selector — pick a model from a priced, numbered menu; cheap by default.

Setup used to default to the most expensive model (opus). This surfaces a small
priced menu per provider so you choose cost-aware, defaults to a mid/cheap tier,
and powers a numbered selector (so `/model` lets you PICK, not just read a list).
Free-text model ids still work — the menu is suggestions, not a lock-in.
"""
from __future__ import annotations

# Per-provider menus: (model id, short label with a cost hint). Ordered
# cheapest→priciest so the menu reads as a ladder; the DEFAULT is a mid tier, and
# never the top (most expensive) row.
_MENUS = {
    "anthropic": [
        ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest, cheapest (~$0.8/$4 per Mtok)"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — balanced, the default (~$3/$15)"),
        ("claude-opus-4-8", "Claude Opus 4.8 — most capable, priciest (~$15/$75)"),
    ],
    "openai": [
        ("gpt-4o-mini", "GPT-4o mini — cheapest (~$0.15/$0.6 per Mtok)"),
        ("gpt-4o", "GPT-4o — balanced, the default (~$2.5/$10)"),
        ("o3", "o3 — strongest reasoning, priciest"),
    ],
    "openrouter": [
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B — cheap, open"),
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 — balanced, the default"),
        ("openai/gpt-4o", "GPT-4o — balanced alternative"),
        ("anthropic/claude-opus-4-8", "Claude Opus 4.8 — most capable, priciest"),
    ],
    "ollama": [
        ("qwen2.5-coder", "Qwen2.5-Coder — local, free, code-tuned (the default)"),
        ("llama3.3", "Llama 3.3 — local, free, general"),
        ("deepseek-r1", "DeepSeek-R1 — local, free, reasoning"),
    ],
}

# The connect-time default per provider — a mid/cheap tier, never the priciest.
_DEFAULT = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-sonnet-4-6",
    "ollama": "qwen2.5-coder",
}


def menu_for(provider_type: str) -> list:
    """A priced menu for a provider: list of ``{"model", "label"}`` rows,
    cheapest first."""
    return [{"model": m, "label": lbl} for (m, lbl) in _MENUS.get(provider_type, [])]


def default_model_for(provider_type: str) -> str:
    """The cost-aware default model id for a provider (mid/cheap tier, never the
    most expensive)."""
    return _DEFAULT.get(provider_type, "claude-sonnet-4-6")


def pick(rows: list, answer: str):
    """Resolve a user's menu answer to a model id. A 1-indexed number selects a
    row; any other non-empty text is taken as a literal model id (no lock-in);
    blank → None."""
    a = (answer or "").strip()
    if not a:
        return None
    if a.isdigit():
        i = int(a)
        if 1 <= i <= len(rows):
            return rows[i - 1]["model"]
        # out-of-range number → treat as a literal id rather than erroring
    return a


def render_menu(rows: list, current: str | None = None) -> str:
    """A numbered, priced menu string, marking the currently-active model."""
    lines = []
    for i, r in enumerate(rows, 1):
        mark = " →" if current and r["model"] == current else ""
        lines.append(f"  {i}. {r['label']}{mark}")
    if current and not any(r["model"] == current for r in rows):
        lines.append(f"  (current: {current})")
    lines.append("pick a number, type any model id, or Enter to keep the current one")
    return "\n".join(lines)
