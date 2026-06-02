"""Structure-aware compact views for verifiable context compression.

korgex's #1 token sink is noisy TOOL OUTPUT (file reads, Bash dumps, browser
snapshots, crawl + subagent results). This module turns a large tool result into
a SHORT, structure-aware view that names what's there (keys, signatures, line
counts) without carrying the bulk. The full original is sealed separately in the
ledger's content-addressed blob store and pulled back byte-for-byte via Retrieve
— so compression only shrinks the MODEL'S view, never loses data.

Design (mirrors web_tools): pure, stdlib-only, offline-testable. A `COMPRESSORS`
registry keyed by detected kind is injectable so a test can swap a compressor.
Heavy/AST work stays optional + lazy (`import ast` only inside compact_python).
Every public function is total — on weird/hostile input it degrades to a
truncated str rather than raising; correctness of the surrounding loop wins.
"""
from __future__ import annotations

import json
from typing import Any

# How much of a long value to surface in a compact view.
_HEAD_LINES = 20
_TAIL_LINES = 10
_SCALAR_PREVIEW = 80       # chars of a long string scalar shown inline
_MAX_KEYS = 40             # cap on dict keys enumerated in a json view
_GIST_CHARS = 120          # one-line gist length for text


def detect_kind(value: Any) -> str:
    """Cheap content sniff → one of {"json", "python", "text"}.

    - dict/list (or a JSON-parseable str) → "json"
    - a str that `ast.parse` accepts as Python → "python"
    - everything else → "text"
    """
    if isinstance(value, (dict, list)):
        return "json"
    if isinstance(value, str):
        s = value.strip()
        if s and s[0] in "{[":
            try:
                json.loads(value)
                return "json"
            except (ValueError, TypeError):
                pass
        # Try Python only if it plausibly looks like code (cheap gate before AST).
        if _looks_like_python(value):
            try:
                import ast
                ast.parse(value)
                return "python"
            except (SyntaxError, ValueError, TypeError):
                pass
        return "text"
    # bytes / numbers / None / anything else → text view of its repr
    return "text"


def _looks_like_python(s: str) -> bool:
    """Cheap heuristic so we don't AST-parse every plain-text blob."""
    head = s.lstrip()[:400]
    return any(
        tok in head
        for tok in ("def ", "class ", "import ", "from ", "async def ", "@")
    )


def _truncate_scalar(value: Any, limit: int = _SCALAR_PREVIEW) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…(+{len(value) - limit} chars)"
    return value


def _shape(value: Any, depth: int = 0) -> Any:
    """Recursive shape summary: containers → type+size; scalars → truncated value."""
    if depth >= 3:
        return "…"
    if isinstance(value, dict):
        keys = list(value.keys())
        out: dict[str, Any] = {}
        for k in keys[:_MAX_KEYS]:
            out[str(k)] = _shape(value[k], depth + 1)
        summary: dict[str, Any] = {"_type": "object", "_keys": len(keys), **out}
        if len(keys) > _MAX_KEYS:
            summary["_more_keys"] = len(keys) - _MAX_KEYS
        return summary
    if isinstance(value, (list, tuple)):
        n = len(value)
        sample = _shape(value[0], depth + 1) if n else None
        return {"_type": "array", "_len": n, "_sample": sample}
    if isinstance(value, str):
        return _truncate_scalar(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_scalar(str(value))


def compact_json(obj: Any) -> str:
    """Collapse a structured value to its SHAPE: top-level keys, container
    sizes, value types, with large scalars truncated. Pure; never raises."""
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        shape = _shape(obj)
        return json.dumps(shape, separators=(",", ":"), default=str)
    except Exception:
        return compact_text(obj if isinstance(obj, str) else str(obj))


def _first_docline(node) -> str | None:
    try:
        import ast
        doc = ast.get_docstring(node)
    except Exception:
        return None
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    return first[:_SCALAR_PREVIEW] if first else None


def _sig(node) -> str:
    """Render a def/class header line (args only, no body)."""
    import ast
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(_name(b) for b in node.bases)
        return f"class {node.name}({bases}):" if bases else f"class {node.name}:"
    # function
    a = node.args
    parts: list[str] = [arg.arg for arg in getattr(a, "posonlyargs", [])]
    if parts:
        parts.append("/")
    parts += [arg.arg for arg in a.args]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    parts += [arg.arg for arg in a.kwonlyargs]
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)}):"


def _name(node) -> str:
    import ast
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def compact_python(src: str) -> str:
    """A skeleton: module/class/def signatures + docstring first lines, bodies
    dropped. Lazy `import ast`; on SyntaxError fall back to compact_text."""
    try:
        import ast
        tree = ast.parse(src)
    except (SyntaxError, ValueError, TypeError):
        return compact_text(src)
    except Exception:
        return compact_text(src)

    lines: list[str] = []
    total = src.count("\n") + 1
    lines.append(f"# python skeleton ({total} lines)")
    module_doc = _first_docline(tree)
    if module_doc:
        lines.append(f'"""{module_doc}"""')

    def walk(body, indent: int) -> None:
        pad = "    " * indent
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lines.append(pad + _sig(node))
                doc = _first_docline(node)
                if doc:
                    lines.append(pad + f'    """{doc}"""')
                if isinstance(node, ast.ClassDef):
                    walk(node.body, indent + 1)
                else:
                    lines.append(pad + "    ...")
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and indent == 0:
                try:
                    lines.append(pad + ast.unparse(node))
                except Exception:
                    pass
            elif isinstance(node, ast.Assign) and indent == 0:
                names = [_name(t) for t in node.targets]
                lines.append(pad + ", ".join(names) + " = ...")

    walk(tree.body, 0)
    return "\n".join(lines)


def compact_text(s: Any) -> str:
    """head N + tail M lines + total line/byte counts + a one-line gist."""
    if not isinstance(s, str):
        try:
            s = s.decode("utf-8", "replace") if isinstance(s, (bytes, bytearray)) else str(s)
        except Exception:
            s = repr(s)
    lines = s.splitlines()
    n = len(lines)
    nbytes = len(s.encode("utf-8", "replace"))
    gist = " ".join(s.split())[:_GIST_CHARS]
    if n <= _HEAD_LINES + _TAIL_LINES:
        body = s
    else:
        head = "\n".join(lines[:_HEAD_LINES])
        tail = "\n".join(lines[-_TAIL_LINES:])
        body = f"{head}\n… [{n - _HEAD_LINES - _TAIL_LINES} lines elided] …\n{tail}"
    return (
        f"[text view] {n} lines, {nbytes} bytes\n"
        f"gist: {gist}\n"
        f"{body}"
    )


# Registry keyed by detected kind — injectable for tests (web_tools _get style).
COMPRESSORS = {
    "json": compact_json,
    "python": compact_python,
    "text": compact_text,
}


def compact_view(value: Any) -> str:
    """Dispatch to the right compressor by detected kind. Pure; never raises."""
    try:
        kind = detect_kind(value)
        compressor = COMPRESSORS.get(kind, compact_text)
        return compressor(value)
    except Exception:
        try:
            return compact_text(str(value))
        except Exception:
            return "[uncompressible value]"
