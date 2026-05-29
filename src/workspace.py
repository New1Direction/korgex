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


def changed_paths(worktree: str) -> list:
    """Repo-relative paths the agent changed in the worktree (modified + new),
    from `git status --porcelain`. Feeds the Gate G merge gate."""
    # --untracked-files=all so a new file in a new dir is listed individually
    # (plain --porcelain collapses it to the directory, hiding the real path).
    out = subprocess.run(["git", "-C", worktree, "status", "--porcelain", "--untracked-files=all"],
                         capture_output=True, text=True).stdout
    paths = []
    for line in out.splitlines():
        if len(line) > 3:
            p = line[3:].strip().strip('"')
            if " -> " in p:  # rename: take the destination
                p = p.split(" -> ", 1)[1]
            paths.append(p)
    return paths


def remove_worktree(repo_root: str, worktree_path: str) -> None:
    """Remove a worktree and prune the registration. Best-effort; never raises."""
    subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", worktree_path],
                   capture_output=True, text=True)
    if os.path.isdir(worktree_path):
        shutil.rmtree(worktree_path, ignore_errors=True)
    subprocess.run(["git", "-C", repo_root, "worktree", "prune"],
                   capture_output=True, text=True)


# ── checkpoint + rewind: bind a ledger seq to a git tree state (Gate C) ───

# Pinned identity so checkpoint commits never trip "tell me who you are".
_CKPT_ID = ["-c", "user.email=korgex-checkpoint@local", "-c", "user.name=korgex-checkpoint"]


def git_checkpoint(worktree: str, message: str = "korgex-checkpoint") -> str:
    """Commit the worktree's CURRENT full state (tracked + new) and return the SHA.

    Captures everything via `add -A` so a later restore can reconstruct the exact
    tree — including files the agent just created.
    """
    subprocess.run(["git", "-C", worktree, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", worktree, *_CKPT_ID, "commit", "--allow-empty", "-q", "-m", message],
                   check=True, capture_output=True)
    return subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def git_restore(worktree: str, sha: str) -> None:
    """Restore the worktree to a checkpoint SHA: revert tracked files (reset --hard)
    and drop anything created since (clean -fd)."""
    subprocess.run(["git", "-C", worktree, "reset", "--hard", sha, "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", worktree, "clean", "-fdq"],
                   check=True, capture_output=True)


class WorkspaceCheckpointer:
    """Maps ledger seq_ids to git checkpoints so a rewind restores BOTH the
    filesystem and the ledger. Each snapshot(seq) records the worktree's tree;
    rewind_to(seq) restores to the latest checkpoint at-or-before that seq and
    truncates the event list (via korg_ledger.rewind_events)."""

    def __init__(self, worktree: str) -> None:
        self.worktree = worktree
        self._checkpoints = []  # list of (seq_id, sha), append-ordered

    def snapshot(self, seq: int) -> str:
        sha = git_checkpoint(self.worktree)
        self._checkpoints.append((seq, sha))
        return sha

    def rewind_to(self, target_seq: int, events: list = None) -> dict:
        """Restore the worktree to the latest checkpoint with seq <= target_seq
        and truncate `events` to that seq. Returns {restored_to, events}."""
        candidates = [(s, h) for (s, h) in self._checkpoints if s <= target_seq]
        restored = candidates[-1] if candidates else None
        if restored is not None:
            git_restore(self.worktree, restored[1])

        truncated = events
        if events is not None:
            from src.korg_ledger import rewind_events
            truncated = rewind_events(events, target_seq)

        return {"restored_to": restored, "events": truncated}
