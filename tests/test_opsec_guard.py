"""The opsec pre-commit guard must block RE / vendor-internal artifacts WITHOUT
false-positiving on legitimate provider integrations.

NousResearch (inference-api.nousresearch.com, OAuth client_id `hermes-cli`) is a
paid provider the user subscribes to; its BYO-OAuth client is normal interop —
the same shape as the grok and gemini clients. The guard's old `\\bhermes\\b` /
`nousresearch` word-matches flagged it. Tightened to RE-ARTIFACT signals only,
the guard now lets provider names/domains through while still catching real leaks.

This drives the hook directly against staged content in a throwaway git repo.
"""
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "githooks" / "pre-commit"


def _run_guard(tmp_path, filename, content) -> int:
    """Stage `content` as a new file in a temp repo, run the hook, return its
    exit code (0 = allowed, 1 = blocked)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=tmp_path, check=True)
    env = {k: v for k, v in os.environ.items() if k != "OPSEC_GUARD_OK"}
    r = subprocess.run(["bash", str(HOOK)], cwd=tmp_path,
                       capture_output=True, text=True, env=env)
    return r.returncode


# ── legitimate provider integrations must PASS ──────────────────────────

def test_guard_allows_nousresearch_provider_client(tmp_path):
    content = (
        'BASE_URL = "https://inference-api.nousresearch.com/v1/chat/completions"\n'
        'AUTH_JSON = "~/.hermes/auth.json"\n'
        'PORTAL_URL = "https://portal.nousresearch.com"\n'
        'CLIENT_ID = "hermes-cli"  # Hermes Agent CLI OAuth (BYO, paid sub)\n'
    )
    assert _run_guard(tmp_path, "model_router.py", content) == 0


def test_guard_allows_grok_and_gemini_endpoints(tmp_path):
    content = (
        'GROK = "https://api.x.ai/v1"\n'
        'GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"\n'
    )
    assert _run_guard(tmp_path, "providers.py", content) == 0


# ── genuine RE artifacts must STILL be blocked ──────────────────────────

def test_guard_blocks_captured_prompt(tmp_path):
    assert _run_guard(tmp_path, "notes.md", "captured from claude max\n") == 1


def test_guard_blocks_hermes_tools_module(tmp_path):
    # the RE module name (underscore) stays blocked — distinct from a provider name
    assert _run_guard(tmp_path, "x.py", "import hermes_tools\n") == 1


def test_guard_blocks_reverse_engineering_and_mitm(tmp_path):
    assert _run_guard(tmp_path, "a.py", "# reverse-engineered the wire protocol\n") == 1
    assert _run_guard(tmp_path, "b.py", "import mitmproxy\n") == 1


def test_guard_blocks_re_findings_filename(tmp_path):
    assert _run_guard(tmp_path, "RE_FINDINGS.md", "anything\n") == 1


def test_guard_blocks_ghidra_project_files(tmp_path):
    # Ghidra / pyghidra-mcp RE output must not enter the public repo.
    # Blocked by filename (.gpr) and by content ("ghidra").
    assert _run_guard(tmp_path, "my_project.gpr", "binary\n") == 1
    assert _run_guard(tmp_path, "notes.py", "# opened in ghidra to inspect the binary\n") == 1
