"""Install skills from the open Agent-Skills catalog.

korgex skills are the Anthropic Agent-Skills format — a directory with a ``SKILL.md``
(YAML frontmatter + markdown body). That's the *same* format published across public
GitHub (``anthropics/skills``, ``vercel-labs/agent-skills``, …) and indexed by
skills.sh, so the whole ecosystem is consumable with no adapter. This module turns a
**ref** into installed skills:

  - ``./path`` · ``/abs`` · ``~/dir`` → a local directory
  - ``https://…`` · ``git@…`` · ``….git`` → a git repo
  - ``owner/repo`` · ``owner/repo@skill`` → a skills.sh shorthand that resolves to the
    GitHub repo (a skills.sh ref is just a public GitHub ``SKILL.md`` repo)

Each found skill is copied into the store as ``trust: installed`` with a ``source:``
provenance stamp. Network (git clone, the skills.sh HTTP search) is **injected** so the
logic is fully unit-testable offline; the CLI supplies real implementations.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

from src.skills import _parse_frontmatter

_LOCAL_PREFIXES = ("./", "../", "/", "~")
_REGISTRY_RE = re.compile(r"^[\w.-]+/[\w.-]+(@[\w.-]+)?$")
_SKILLSSH_SEARCH = "https://skills.sh/api/search"


# ── ref resolution ──────────────────────────────────────────────────────────────

def resolve_ref(ref: str):
    """Classify an install ref. Returns ``(kind, ref)`` where kind is
    ``local`` | ``git`` | ``registry``. Raises ValueError on an unrecognizable ref."""
    r = (ref or "").strip()
    if r.startswith(_LOCAL_PREFIXES):
        return "local", r
    if "://" in r or r.startswith("git@") or r.endswith(".git"):
        return "git", r
    if _REGISTRY_RE.match(r):
        return "registry", r
    if os.path.exists(r):  # a bare existing path
        return "local", r
    raise ValueError(f"unrecognized skill ref: {ref!r} "
                     "(use ./path, a git URL, or owner/repo[@skill])")


def registry_to_git(ref: str):
    """``owner/repo[@skill]`` → ``(git_url, subpath_or_None)``. A skills.sh ref is just a
    public GitHub repo, optionally with a sub-skill path after ``@``."""
    repo, _, skill = ref.partition("@")
    return f"https://github.com/{repo}.git", (skill or None)


# ── finding + (de)serializing skills ────────────────────────────────────────────

def _skill_md(skill_dir: str):
    """Path to the skill marker in a dir (SKILL.md preferred, legacy skill.md), or None."""
    for marker in ("SKILL.md", "skill.md"):
        p = os.path.join(skill_dir, marker)
        if os.path.isfile(p):
            return p
    return None


def find_skill_dirs(root: str) -> list:
    """Every directory at/under ``root`` that holds a SKILL.md (or legacy skill.md).
    The root itself counts if it's a skill; otherwise we scan one level of children
    and, failing that, walk the tree (a repo may nest skills a few levels deep)."""
    if _skill_md(root):
        return [root]
    found = []
    try:
        for entry in sorted(os.listdir(root)):
            child = os.path.join(root, entry)
            if os.path.isdir(child) and _skill_md(child):
                found.append(child)
    except OSError:
        return []
    if found:
        return found
    # Nothing one level down — walk deeper (capped naturally by the tree).
    for dirpath, _dirs, _files in os.walk(root):
        if dirpath != root and _skill_md(dirpath):
            found.append(dirpath)
    return found


def serialize_skill(meta: dict, body: str) -> str:
    """Render a SKILL.md from frontmatter + body — round-trips with
    ``skills._parse_frontmatter`` (simple ``key: value`` lines)."""
    lines = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return f"---\n{lines}\n---\n\n{body}\n"


def _install_one(skill_dir: str, dest_root: str, *, source: str | None = None) -> str:
    """Copy one skill dir into the store as ``trust: installed`` (+ source stamp).
    Returns the skill's name. The whole dir is copied (scripts/refs come along); only
    the SKILL.md's frontmatter is rewritten."""
    md = _skill_md(skill_dir)
    text = open(md, encoding="utf-8").read()
    meta, body = _parse_frontmatter(text)
    meta = dict(meta or {})
    name = (meta.get("name") or os.path.basename(skill_dir.rstrip("/")) or "skill").strip()
    meta["name"] = name
    meta["trust"] = "installed"
    if source:
        meta["source"] = source
    out_dir = os.path.join(dest_root, _safe_name(name))
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(skill_dir, out_dir)
    # Write the canonical SKILL.md with rewritten frontmatter.
    upper = os.path.join(out_dir, "SKILL.md")
    with open(upper, "w", encoding="utf-8") as f:
        f.write(serialize_skill(meta, body))
    # Drop a legacy lowercase marker on case-sensitive filesystems (on a
    # case-insensitive FS skill.md *is* SKILL.md — don't delete what we just wrote).
    legacy = os.path.join(out_dir, "skill.md")
    if os.path.isfile(legacy) and not (os.path.exists(upper) and os.path.samefile(legacy, upper)):
        try:
            os.remove(legacy)
        except OSError:
            pass
    return name


def _safe_name(name: str) -> str:
    """A filesystem-safe directory name derived from the skill name."""
    safe = re.sub(r"[^\w.-]+", "-", name).strip("-")
    return safe or "skill"


# ── install / adopt ─────────────────────────────────────────────────────────────

def _git_clone(git_url: str, subpath):
    """Shallow-clone ``git_url`` to a temp dir; return the dir (or its subpath). The
    caller owns cleanup via the returned path's tempdir parent — we leak the tempdir
    deliberately for the process lifetime (small, and simpler than a context manager
    threaded through install())."""
    tmp = tempfile.mkdtemp(prefix="korgex-skill-")
    subprocess.run(["git", "clone", "--depth", "1", git_url, tmp],
                   check=True, capture_output=True, text=True)
    return os.path.join(tmp, subpath) if subpath else tmp


def install(ref: str, dest_root: str, *, clone=None, source_label: str | None = None) -> list:
    """Install every skill found under ``ref`` into ``dest_root`` as trust:installed.

    ``clone(git_url, subpath) -> local_dir`` is injected for git/registry refs (default
    = a real shallow ``git clone``). ``source_label`` overrides the recorded provenance
    (default: the resolved source). Returns the installed skill names."""
    kind, r = resolve_ref(ref)
    if kind == "local":
        src_root = os.path.expanduser(r)
        source = source_label or f"local:{r}"
    else:
        if kind == "registry":
            git_url, subpath = registry_to_git(r)
        else:
            git_url, subpath = r, None
        src_root = (clone or _git_clone)(git_url, subpath)
        source = source_label or git_url
    os.makedirs(dest_root, exist_ok=True)
    return [_install_one(d, dest_root, source=source) for d in find_skill_dirs(src_root)]


def adopt(src_dir: str, dest_root: str) -> list:
    """Import every skill already on disk under ``src_dir`` (e.g. ``~/.claude/skills``)
    into korgex's store as trust:installed. The interop move: any agent's on-disk
    Agent-Skills become korgex-managed without a re-download."""
    src = os.path.expanduser(src_dir)
    os.makedirs(dest_root, exist_ok=True)
    return [_install_one(d, dest_root, source=f"adopt:{src_dir}")
            for d in find_skill_dirs(src)]


# ── export: push korgex skills out to other agents ──────────────────────────────

# Project-relative skills dirs for the common agents (matches their on-disk layout).
# Same Agent-Skills format, so an exported korgex skill is directly usable.
KNOWN_AGENT_TARGETS = {
    "claude": ".claude/skills",
    "cursor": ".cursor/skills",
    "codex": ".codex/skills",
    "opencode": ".opencode/skills",
}


def resolve_export_target(target: str, project_root: str) -> str:
    """Map an export target to a directory: a known agent alias → its project-local
    skills dir; anything else is treated as a literal directory path (``~`` expanded)."""
    rel = KNOWN_AGENT_TARGETS.get((target or "").strip().lower())
    if rel:
        return os.path.join(project_root, *rel.split("/"))
    return os.path.expanduser(target)


def export_skill(skill_dir: str, target_dir: str, name: str) -> str:
    """Copy a korgex skill directory into ``target_dir/<name>``; return the destination.
    The skill is left in the shared Agent-Skills format so the other agent can read it
    directly (it just ignores korgex's extra ``trust``/``source`` frontmatter keys)."""
    out = os.path.join(target_dir, _safe_name(name))
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copytree(skill_dir, out)
    return out


def export_skills(names, target_dir: str, registry) -> list:
    """Export each named skill (resolved from a loaded ``SkillRegistry``) into
    ``target_dir``. Unknown names are skipped. Returns ``[(name, dest), …]`` — the
    move that makes korgex's self-learned skills usable by other agents."""
    done = []
    for name in names:
        sk = registry.get(name)
        if sk is None:
            continue
        dest = export_skill(os.path.dirname(sk.path), target_dir, sk.name)
        done.append((sk.name, dest))
    return done


# ── skills.sh search ────────────────────────────────────────────────────────────

def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — fixed https host
        return resp.read().decode("utf-8", "replace")


def search(query: str, *, http_get=None, limit: int = 20) -> list:
    """Search the skills.sh catalog. Returns ``[{source, skillId, name, installs}, …]``
    — public GitHub repos korgex can then ``install``. Tolerant of a bad response
    (returns ``[]``, never raises). ``http_get(url) -> str`` is injected for testing."""
    url = f"{_SKILLSSH_SEARCH}?{urllib.parse.urlencode({'q': query, 'limit': limit})}"
    try:
        raw = (http_get or _http_get)(url)
        data = json.loads(raw)
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return []
    except Exception:
        return []
    out = []
    for r in results[:limit]:
        if not isinstance(r, dict):
            continue
        out.append({
            "source": r.get("source") or "",
            "skillId": r.get("skillId") or r.get("id") or "",
            "name": r.get("name") or "",
            "installs": int(r.get("installs") or 0),
        })
    return out
