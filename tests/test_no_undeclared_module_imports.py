"""Guard against the v0.5.0 PyYAML / v0.6.2 requests class of bug.

A *module-level* (import-time) third-party import that isn't a declared
dependency crashes ``korgex`` on any clean install where that package happens
to be absent — even though the local dev env (which has it transitively, e.g.
``requests`` via twine) masks the failure entirely.

This test fails if any bare top-level import in ``src/`` is provided by an
*installed distribution that pyproject does not declare*. It would have caught
both the PyYAML regression (v0.5.0–v0.6.0) and the requests regression (v0.6.1).

Deliberately scoped to MODULE-LEVEL imports: lazy in-function imports of heavy
optional deps (numpy, modal, selenium, jsonschema, fastembed) are a valid
"optional feature" pattern and belong in [project.optional-dependencies]; they
never fire on ``korgex --version`` or a normal run. ``try/import/except`` blocks
are also exempt — their imports live inside a Try node, not the module body.

3.9-compatible: no tomllib / sys.stdlib_module_names / packages_distributions.
"""
from __future__ import annotations

import ast
import importlib.metadata as im
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PYPROJECT = REPO / "pyproject.toml"


def _canon(name: str) -> str:
    """PEP 503 canonical distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_deps() -> set[str]:
    """Canonical names from pyproject [project].dependencies (regex, no tomllib)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    assert block, "could not locate [project].dependencies in pyproject.toml"
    names = re.findall(r'"([^"]+)"', block.group(1))
    return {_canon(re.split(r"[<>=!~;\[\s]", n, 1)[0]) for n in names}


def _resolves_into_stdlib(root: str) -> bool:
    """True if `import <root>` resolves to a builtin or a stdlib file (not site-packages).

    Distinguishes the real stdlib ``dataclasses`` (3.10+ / shadowing on 3.9) from a
    stray PyPI backport distribution of the same name, without sys.stdlib_module_names.
    """
    try:
        spec = importlib.util.find_spec(root)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    origin = spec.origin
    if origin in (None, "built-in", "frozen"):
        return True  # builtin / namespace package
    return "site-packages" not in origin and "dist-packages" not in origin


def _root_to_dists() -> dict[str, set[str]]:
    """Map each importable top-level name -> set of installed dists providing it."""
    mapping: dict[str, set[str]] = {}
    for dist in im.distributions():
        dname = _canon(dist.metadata["Name"])
        tops = (dist.read_text("top_level.txt") or "").split()
        if not tops and dist.files:
            tops = sorted(
                {f.parts[0] for f in dist.files if f.suffix == ".py" and len(f.parts) > 1}
            )
        for t in tops:
            mapping.setdefault(t, set()).add(dname)
    return mapping


def _module_level_import_roots() -> dict[str, set[str]]:
    """Top-level (bare, non-relative, non-try-guarded) import roots across src/."""
    roots: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in tree.body:  # direct children only -> excludes in-function + try-guarded
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue  # relative import
                names = [node.module.split(".")[0]]
            else:
                continue
            for n in names:
                roots.setdefault(n, set()).add(str(path.relative_to(REPO)))
    return roots


def test_no_undeclared_module_level_imports():
    declared = _declared_deps()
    root_to_dists = _root_to_dists()

    offenders = {}
    for root, files in _module_level_import_roots().items():
        if root == "src":  # first-party
            continue
        if _resolves_into_stdlib(root):  # e.g. real stdlib dataclasses on 3.9/3.10+
            continue
        providing = root_to_dists.get(root)
        if not providing:
            # stdlib, or genuinely-absent: an absent module-level import would
            # already crash collection loudly, so it can't slip past silently.
            continue
        if providing.isdisjoint(declared):
            offenders[root] = (sorted(providing), sorted(files))

    assert not offenders, (
        "module-level imports provided by undeclared distributions "
        "(add them to pyproject [project].dependencies or make the import lazy):\n"
        + "\n".join(
            f"  import {root!r} <- dist {dists} ; used in {files}"
            for root, (dists, files) in sorted(offenders.items())
        )
    )
