"""Publish a verifiable receipt as a hosted, shareable proof page.

`korgex receipt share <file> --publish` renders the self-verifying page and writes it into
a configured static-site checkout (a GitHub Pages repo) under ``r/<id>.html``, then
git-pushes it — so it's reachable at a real URL like ``https://yvaehkorg.lol/r/<id>.html``.
Served as real HTML, the link unfurls as a social card AND re-verifies in the recipient's
browser. The id is the receipt's chain tip (content-addressed → same receipt = same stable
URL). This closes the viral loop for provable agent work, with zero new infrastructure: it
reuses a static host you already have and your existing git auth.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def publish_id(receipt: dict) -> str:
    """A stable, content-addressed id for a receipt: the first 12 hex of its chain tip."""
    tip = receipt.get("tip") or ""
    return tip[:12] if tip else "receipt"


def share_url(base_url: str, rid: str, subdir: str = "r") -> str:
    """The public URL a published page will live at (base_url's trailing slash normalized)."""
    return f"{(base_url or '').rstrip('/')}/{subdir}/{rid}.html"


def publish_receipt(receipt: dict, *, repo_dir: str, base_url: str,
                    og_image: str | None = None, subdir: str = "r") -> dict:
    """Render the receipt's self-verifying page — with its own public URL baked into the
    social card — and write it into ``repo_dir/<subdir>/<id>.html``. Returns
    ``{id, url, path, rel_path}``. Does NOT git-push (call ``git_deploy`` for that), so the
    render+write stays pure and testable on its own."""
    from src import receipt as RC

    rid = publish_id(receipt)
    url = share_url(base_url, rid, subdir)
    rel_path = f"{subdir}/{rid}.html"
    dest = Path(repo_dir) / subdir / f"{rid}.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # base_url = the page's OWN public URL → og:url is canonical for this exact page.
    dest.write_text(RC.render_html(receipt, og_image=og_image, base_url=url), encoding="utf-8")
    return {"id": rid, "url": url, "path": str(dest), "rel_path": rel_path}


def git_deploy(repo_dir: str, rel_path: str, message: str) -> bool:
    """Best-effort: stage `rel_path`, commit, and push to origin from `repo_dir`. Returns
    True on a successful push, False on any failure (not a git repo, no remote, auth) —
    never raises. Uses the caller's existing git credentials; an idempotent re-publish of an
    unchanged page (nothing to commit, remote already current) counts as success."""
    def _git(*args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", repo_dir, *args],
                              capture_output=True, text=True, timeout=120)
    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return False
        if _git("add", rel_path).returncode != 0:
            return False
        committed = _git("commit", "-m", message).returncode == 0  # no-op if unchanged
        push = _git("push", "origin", "HEAD")
        if push.returncode == 0:
            return True
        # unchanged page + already-current remote → still "deployed"
        return (not committed) and "up to date" in (push.stderr + push.stdout).lower()
    except Exception:
        return False
