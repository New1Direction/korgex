"""Destructive-command guard — a semantic safety FLOOR for Bash.

korgex's gate pipeline is path-based; Bash command *strings* were never inspected,
so under the FREE-by-default policy a `rm -rf /`, `dd of=/dev/sda`, fork bomb, or
`curl | sh` ran unchecked. ``assess_command(cmd)`` is the missing detector: it
returns ``None`` for anything it doesn't recognize as catastrophic (WHITELIST-FIRST,
default-ALLOW) or a verdict dict ``{category, reason, matched, severity}`` for a
clearly-destructive command.

False-positive control is the whole game (this runs on by default):
  - the command is split into SIMPLE commands quote-/operator-aware (shlex with
    punctuation_chars), so a dangerous string inside QUOTED DATA or a COMMENT is
    never the program token and never fires (``echo "rm -rf /"`` is safe);
  - rules match on the program + its args, with explicit SAFE exceptions
    (``rm -rf ./build`` / ``/tmp/...`` pass; only catastrophic targets flag);
  - a shell ``-c`` payload recurses ONE level (``bash -c 'rm -rf /'`` flags), but a
    non-shell ``-c`` (``python -c "..."``) is treated as data.

This is a floor against ACCIDENTS, not a sandbox: base64|sh, ``$(...)`` indirection,
and other obfuscation evade regex by design. Always fail-OPEN — a parse error or any
exception means "allow" (never crash the agent loop). Pure + stdlib-only.
"""
from __future__ import annotations

import re
import shlex
from typing import Optional

# Shells whose ``-c`` payload is itself a command we recurse into.
_SHELLS = {"sh", "bash", "zsh", "dash", "ash", "ksh"}
# Prefixes that wrap a real command — peel them to find the actual program.
_WRAPPERS = {"sudo", "doas", "command", "nice", "nohup", "time", "exec", "env",
             "xargs", "stdbuf", "ionice", "setsid"}


def _simple_commands(command: str) -> list[list[str]]:
    """Split into SIMPLE commands (argv lists), quote- and operator-aware.

    shlex with ``punctuation_chars`` tokenizes ``; | & && || ( ) < >`` as their own
    tokens and strips ``#`` comments, while keeping quoted strings as single tokens.
    We then break the token stream on shell control operators. Raises propagate to
    the caller, which fails open."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    cmds: list[list[str]] = []
    cur: list[str] = []
    for tok in lex:
        if tok and all(c in ";|&()<>" for c in tok):  # an operator/punctuation run
            if cur:
                cmds.append(cur)
                cur = []
            continue
        cur.append(tok)
    if cur:
        cmds.append(cur)
    return cmds


def _program(argv: list[str]) -> tuple[Optional[str], list[str]]:
    """Peel wrappers (sudo/env/...) and leading VAR=val assignments; return
    ``(program, args)`` with the program basename lowered, or ``(None, [])``."""
    i = 0
    while i < len(argv):
        a = argv[i]
        base = a.rsplit("/", 1)[-1]
        if base in _WRAPPERS:
            i += 1
            continue
        if "=" in a and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a):  # FOO=bar prefix
            i += 1
            continue
        return base.lower(), argv[i + 1:]
    return None, []


# ── rm: recursive delete of a catastrophic target ───────────────────────────
def _rm_target_dangerous(t: str) -> bool:
    t = t.strip()
    if t in (".", "./", "*", "./*", "~", "~/", "/", "/*"):
        return True
    if re.match(r"^\$\{?HOME\}?/?$", t):                       # $HOME / ${HOME}
        return True
    if re.match(r"^/(tmp|var/tmp)(/|$)", t):                   # tmp is a safe exception
        return False
    if re.match(r"^/(etc|usr|bin|sbin|lib|lib64|var|boot|dev|sys|proc|root|home|opt)(/|$)", t):
        return True
    return False


def _check_rm(args: list[str]) -> Optional[dict]:
    recursive = any(
        a == "--recursive" or (a.startswith("-") and not a.startswith("--") and "r" in a.lower())
        for a in args)
    if not recursive:
        return None
    targets = [a for a in args if not a.startswith("-")]
    for t in targets:
        if _rm_target_dangerous(t):
            return _v("filesystem", f"recursive delete of a critical path: rm … {t}",
                      f"rm {t}", "critical")
    return None


# ── disk: dd to a device, mkfs/wipefs/fdisk/shred on a device ────────────────
_DEVICE = re.compile(r"^/dev/(sd|nvme|hd|disk|mmcblk|vd|xvd|loop)", re.IGNORECASE)


def _check_disk(program: str, args: list[str]) -> Optional[dict]:
    if program == "dd":
        for a in args:
            if a.startswith("of=") and _DEVICE.match(a[3:]):
                return _v("disk", f"dd writing directly to a block device ({a})", a, "critical")
        return None
    if program in ("mkfs", "wipefs", "fdisk", "sgdisk", "parted", "shred") or program.startswith("mkfs."):
        for a in args:
            if _DEVICE.match(a):
                return _v("disk", f"{program} on a block device ({a}) destroys data", a, "critical")
    return None


# ── permissions: chmod/chown -R on root or 777 on root ───────────────────────
def _check_perms(program: str, args: list[str]) -> Optional[dict]:
    if program not in ("chmod", "chown"):
        return None
    recursive = any(a in ("-R", "--recursive") or (a.startswith("-") and "R" in a) for a in args)
    targets = [a for a in args if not a.startswith("-") and "=" not in a]
    # the mode/owner token is the first non-flag; the rest are paths
    paths = targets[1:] if targets else []
    hits_root = any(p in ("/", "/*") or re.match(r"^/(etc|usr|bin|boot|lib)", p) for p in paths)
    if recursive and hits_root:
        return _v("permissions", f"{program} -R on a system root ({' '.join(paths)})",
                  f"{program} -R", "high")
    if program == "chmod" and "777" in (targets[0] if targets else "") and hits_root:
        return _v("permissions", "chmod 777 on a system root", "chmod 777 /", "high")
    return None


# ── git: history / uncommitted-work loss ─────────────────────────────────────
def _check_git(args: list[str]) -> Optional[dict]:
    if not args:
        return None
    sub = args[0]
    rest = set(args[1:])
    if sub == "push" and (rest & {"--force", "-f"}):
        return _v("git", "git push --force can overwrite remote history", "git push --force", "high")
    if sub == "reset" and "--hard" in rest:
        return _v("git", "git reset --hard discards uncommitted work", "git reset --hard", "high")
    if sub == "clean" and any(("f" in a and ("d" in a or "x" in a)) for a in args[1:] if a.startswith("-")):
        return _v("git", "git clean -fd/-fdx deletes untracked files", "git clean -fdx", "high")
    if sub == "branch" and "-D" in rest:
        return _v("git", "git branch -D force-deletes a branch", "git branch -D", "medium")
    if sub == "stash" and (rest & {"drop", "clear"}):
        return _v("git", "git stash drop/clear discards stashed work", "git stash drop", "medium")
    return None


# ── db clients: DROP / TRUNCATE ──────────────────────────────────────────────
_DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongo", "mongosh", "cqlsh", "dropdb"}
_SQL_DESTRUCTIVE = re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+table?)\b", re.IGNORECASE)


def _check_db(program: str, args: list[str]) -> Optional[dict]:
    if program == "dropdb":
        return _v("database", "dropdb destroys an entire database", "dropdb", "high")
    if program in _DB_CLIENTS:
        for a in args:
            if _SQL_DESTRUCTIVE.search(a):
                return _v("database", f"destructive SQL via {program}: {a[:60]}", a[:60], "high")
    return None


# ── container/orchestrator/cloud: the clearest wipes ─────────────────────────
def _check_infra(program: str, args: list[str]) -> Optional[dict]:
    joined = " ".join(args)
    if program == "docker" and re.search(r"\bsystem\s+prune\b.*(-a|--all)|\bvolume\s+rm\b", joined):
        return _v("infra", "docker prune/volume rm destroys images/volumes", "docker prune", "medium")
    if program == "kubectl" and re.search(r"\bdelete\b.*(namespace\b|--all\b)", joined):
        return _v("infra", "kubectl delete namespace/--all is broad and destructive",
                  "kubectl delete", "high")
    if program == "aws" and re.search(r"\bec2\s+terminate-instances\b|\bs3\s+rb\b.*--force", joined):
        return _v("infra", "aws terminate/rb --force is irreversible", "aws", "high")
    return None


# ── structural rules (checked on the RAW command — about pipe/recursion shape) ─
_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+|doas\s+)?(sh|bash|zsh|dash|ash|ksh)\b",
    re.IGNORECASE)
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
_REDIR_DEVICE = re.compile(r">\s*/dev/(sd|nvme|hd|disk|mmcblk|vd|xvd)", re.IGNORECASE)


def _check_structural(command: str) -> Optional[dict]:
    if _FORK_BOMB.search(command):
        return _v("forkbomb", "fork bomb — spawns processes until the machine locks up",
                  ":(){ :|:& };:", "critical")
    if _PIPE_TO_SHELL.search(command):
        return _v("pipe-to-shell", "piping a remote download straight into a shell runs unverified code",
                  "curl … | sh", "high")
    if _REDIR_DEVICE.search(command):
        return _v("disk", "redirecting output onto a block device destroys it", "> /dev/…", "critical")
    return None


def _v(category: str, reason: str, matched: str, severity: str) -> dict:
    return {"category": category, "reason": reason, "matched": matched, "severity": severity}


def _check_argv(argv: list[str], depth: int = 0) -> Optional[dict]:
    program, args = _program(argv)
    if program is None:
        return None
    # Shell -c '<cmd>' — recurse ONE level into the payload (bash -c 'rm -rf /').
    if program in _SHELLS and depth == 0:
        for i, a in enumerate(args):
            if a == "-c" and i + 1 < len(args):
                inner = assess_command(args[i + 1], _depth=depth + 1)
                if inner:
                    return inner
    if program == "rm":
        return _check_rm(args)
    if program == "git":
        return _check_git(args)
    return (_check_disk(program, args) or _check_perms(program, args)
            or _check_db(program, args) or _check_infra(program, args))


def assess_command(command: str, _depth: int = 0) -> Optional[dict]:
    """Return a verdict dict for a clearly-destructive command, else ``None``.

    WHITELIST-FIRST / default-allow. Fails OPEN: any parse error or unexpected
    exception returns ``None`` (allow) so the guard can never crash the loop."""
    if not command or not command.strip():
        return None
    try:
        structural = _check_structural(command)
        if structural:
            return structural
        for argv in _simple_commands(command):
            verdict = _check_argv(argv, depth=_depth)
            if verdict:
                return verdict
    except Exception:
        # Fail-open: a malformed command (e.g. unbalanced quotes) must not raise.
        return None
    return None
