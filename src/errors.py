"""Human-friendly error messages.

Provider/SDK failures arrive cryptic — a bare ``401``, a socket error, a token-limit
string. When a turn dies, the user deserves one clear sentence: what failed and what
to do. ``humanize_error`` matches the common failure modes and returns actionable
guidance; anything unrecognized falls through to the original text, so the real
error is never hidden.
"""
from __future__ import annotations

# (substrings to match in the lowercased error, friendly + actionable message).
# Order matters — first match wins, so put the specific before the generic.
_RULES = [
    (("401", "unauthorized", "invalid api key", "invalid_api_key", "authentication", "no api key"),
     "Authentication failed — the provider rejected your API key. "
     "Re-run `korgex setup` (or check the key/token for this provider)."),
    (("429", "rate limit", "rate_limit", "too many requests"),
     "Rate-limited by the provider. Wait a few seconds and retry, "
     "or switch to a different model with /model."),
    (("insufficient_quota", "exceeded your current quota", "quota", "billing", "insufficient credit",
      "insufficient funds", "payment required", "402"),
     "The provider reports no remaining credit/quota. Top up the account, "
     "or switch providers/models with /model."),
    (("maximum context", "context length", "context_length_exceeded", "too many tokens",
      "reduce the length"),
     "The conversation is too long for this model's context window. "
     "Run /clear to start fresh, or switch to a larger-context model with /model."),
    (("model not found", "does not exist", "no such model", "unknown model", "404", "model_not_found"),
     "That model wasn't found at this provider. Check the model id with /model."),
    (("connection", "timed out", "timeout", "network", "getaddrinfo", "failed to establish",
      "name or service not known", "connection refused", "max retries"),
     "Can't reach the provider — check your internet connection "
     "(or the base URL set in `korgex setup`)."),
]


def humanize_error(err) -> str:
    """Map a raw error (string or Exception) to a clear, actionable message.
    Unrecognized errors are returned as-is so the real cause is never hidden."""
    if err is None:
        return "Something went wrong (no detail available)."
    raw = str(err).strip()
    if not raw:
        return "Something went wrong (no detail available)."
    low = raw.lower()
    for needles, message in _RULES:
        if any(n in low for n in needles):
            return message
    return raw
