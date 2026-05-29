"""
structured_output.py — schema-constrained final answers for korgex (roadmap P0).

korgex's terminal answer was free prose, which then became the unvalidated
`result.text` on the ledger's last `llm_inference` event. This module lets a
caller demand a final answer that conforms to a JSON Schema, so the ledger
event carries a *validated, content-addressed structured object* instead of a
prose blob — making replay/recall/audit queryable by field and results
deduplicable across agents (the same content hashes identically, spec §3).

Two provider shapes are supported:
  - Anthropic: a forced single-tool call (`tool_choice` pins `emit_structured_output`).
  - OpenAI-compatible: `response_format` json_schema.

The provider does best-effort shaping; the real guarantee korgex *owns* is the
client-side `validate()` over the schema. If the model returns something that
doesn't conform, the caller retries, then fails loudly rather than recording a
lie on the ledger.
"""

from __future__ import annotations

import json
from typing import Any

# The synthetic Anthropic tool whose single invocation carries the structured result.
STRUCTURED_TOOL_NAME = "emit_structured_output"


def build_request_kwargs(schema: dict, provider: str) -> dict:
    """Return the provider-specific kwargs that force schema-constrained output.

    Merge the result into the provider's create() call. For Anthropic this
    replaces the tool set with a single forced output tool; for OpenAI it sets
    response_format and offers no tools (the final answer is the object itself).
    """
    if provider == "anthropic":
        return {
            "tools": [{
                "name": STRUCTURED_TOOL_NAME,
                "description": (
                    "Emit the final result as a structured object that conforms "
                    "exactly to the provided schema. Call this tool once."
                ),
                "input_schema": schema,
            }],
            "tool_choice": {"type": "tool", "name": STRUCTURED_TOOL_NAME},
        }

    # OpenAI-compatible (openai, openrouter, ollama, deepseek, ...).
    # strict=False: we do not over-constrain the provider decoder (which rejects
    # schemas lacking additionalProperties:false / full required); client-side
    # validate() is the source of truth.
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": schema,
                "strict": False,
            },
        },
    }


def extract(response: Any, provider: str) -> Any:
    """Pull the raw structured object out of a model response, or None.

    None means "no parseable object was present" — the caller decides whether
    to retry. Validation is a separate step (validate()).
    """
    if response is None:
        return None

    if provider == "anthropic":
        for block in (getattr(response, "content", None) or []):
            if (getattr(block, "type", None) == "tool_use"
                    and getattr(block, "name", None) == STRUCTURED_TOOL_NAME):
                return getattr(block, "input", None)
        return None

    # OpenAI-compatible: the object is JSON in message.content.
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return None
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None


def validate(obj: Any, schema: dict) -> list:
    """Validate `obj` against `schema`. Return a list of error strings ([] == valid).

    This is the guarantee korgex controls regardless of provider behavior.
    """
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema)
    errors = []
    for err in validator.iter_errors(obj):
        path = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors
