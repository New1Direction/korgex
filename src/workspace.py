"""
workspace.py — isolated git worktrees + a path-boundary guard (roadmap Gate A).

The #1 safety fix for letting korgex edit its own (or any) repo autonomously:
self-edits run in a dedicated git WORKTREE on a throwaway branch, never the live
working copy, and `path_within` is the deterministic guard that proves a write
can't escape that worktree (absolute paths, ../ traversal, and symlink escapes
are all rejected via realpath). The live tree is only ever touched when a human
reviews the branch and merges.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile


def path_within(root: str, target: str) -> bool:
    """True iff `target` resolves to a path inside `root`.

    `target` is taken relative to `root` when not absolute. Uses realpath so
    ../ traversal and symlink escapes are caught even if the path doesn't exist
    yet (the common case for a file about to be written).
    """
    root_r = os.path.realpath(root)
    t = target if os.path.isabs(target) else os.path.join(root_r, target)
    t_r = os.path.realpath(t)
    return t_r == root_r or t_r.startswith(root_r + os.sep)


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-").lower() or "task"


def default_worktree_path(repo_root: str, branch: str) -> str:
    """A worktree location OUTSIDE the repo (so the source `git status` stays clean)."""
    base = os.path.basename(os.path.realpath(repo_root)) or "repo"
    return os.path.join(tempfile.gettempdir(), "korgex-worktrees",
                        f"{base}--{_slug(branch)}")


def create_worktree(repo_root: str, branch: str, worktree_path: str = None,
                    base: str = "HEAD") -> str:
    """Create a git worktree for `repo_root` on a new `branch`, checked out from `base`.

    Returns the worktree path. Idempotent-ish: a stale worktree/branch at the
    same location is cleaned first so re-runs don't fail.
    """
    wt = worktree_path or default_worktree_path(repo_root, branch)
    os.makedirs(os.path.dirname(wt), exist_ok=True)

    # Clean any stale registration/dir + branch from a prior run.
    if os.path.exists(wt):
        remove_worktree(repo_root, wt)
    subprocess.run(["git", "-C", repo_root, "branch", "-D", branch],
                   capture_output=True, text=True)  # ignore failure (may not exist)

    proc = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "-b", branch, wt, base],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {proc.stderr.strip()}")
    return wt


def remove_worktree(repo_root: str, worktree_path: str) -> None:
    """Remove a worktree and prune the registration. Best-effort; never raises."""
    subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", worktree_path],
                   capture_output=True, text=True)
    if os.path.isdir(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
    subprocess.run(["git", "-C", repo_root, "worktree", "prune"],
                   capture_output=True, text=True)
