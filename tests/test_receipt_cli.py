"""korgex receipt CLI — mint + verify end-to-end, driving sys.argv like the real CLI."""
import json
from pathlib import Path

from src import cli
from src import korg_ledger as KL
from src import receipt as RC
from src import signing


def _journal(tmp_path):
    jp = str(tmp_path / "journal.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("add a /healthz endpoint")
    c.record_tool_call("Edit", {"file_path": "src/app.py"}, {"ok": True}, True, 9, triggered_by=root)
    return jp


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["korgex", "receipt", *args])
    return cli.cmd_receipt()


def test_mint_writes_verifiable_json_and_self_verifying_html(tmp_path, monkeypatch):
    jp = _journal(tmp_path)
    out, html = str(tmp_path / "r.json"), str(tmp_path / "r.html")
    assert _run(monkeypatch, jp, "--claim", "shipped healthz", "-o", out, "--html", html) == 0
    assert Path(out).exists() and Path(html).exists()
    receipt = json.loads(Path(out).read_text())
    assert receipt["claim"] == "shipped healthz"
    assert RC.verify_receipt(receipt)["ok"] is True
    assert "verifyChain" in Path(html).read_text()


def test_verify_subcommand_passes_then_fails_on_tamper(tmp_path, monkeypatch):
    jp = _journal(tmp_path)
    out = str(tmp_path / "r.json")
    _run(monkeypatch, jp, "-o", out)
    assert _run(monkeypatch, "verify", out) == 0           # intact → exit 0

    receipt = json.loads(Path(out).read_text())
    receipt["events"][0]["args"] = {"prompt": "TAMPERED"}  # edit a recorded event
    Path(out).write_text(json.dumps(receipt))
    assert _run(monkeypatch, "verify", out) == 1           # tampered → nonzero (CI-gateable)


def test_sign_uses_identity_from_env(tmp_path, monkeypatch):
    priv, pub = signing.generate_keypair()
    monkeypatch.setenv("KORGEX_SIGNING_KEY", priv)         # off-disk identity → no real ~/.korgex write
    out = str(tmp_path / "r.json")
    assert _run(monkeypatch, _journal(tmp_path), "--sign", "-o", out) == 0
    receipt = json.loads(Path(out).read_text())
    assert receipt["signature"]["pubkey"] == pub
    assert RC.verify_receipt(receipt)["signature_ok"] is True


def test_missing_journal_is_a_clean_error(tmp_path, monkeypatch):
    assert _run(monkeypatch, str(tmp_path / "nope.jsonl")) == 1


def test_share_subcommand_renders_shareable_page_from_a_receipt(tmp_path, monkeypatch):
    jp = _journal(tmp_path)
    out = str(tmp_path / "r.json")
    _run(monkeypatch, jp, "--claim", "shipped healthz", "-o", out)     # mint a receipt first
    page = str(tmp_path / "share.html")
    assert _run(monkeypatch, "share", out, "-o", page) == 0
    txt = Path(page).read_text()
    assert 'property="og:title"' in txt                                # a real social card (link unfurls)
    assert "shipped healthz" in txt
    assert "verifyChain" in txt                                        # still self-verifying in-browser
    assert "korg-verify" in txt                                        # and offers independent re-checking


def test_share_missing_file_is_a_clean_error(tmp_path, monkeypatch):
    assert _run(monkeypatch, "share", str(tmp_path / "nope.json")) == 1


def test_share_publish_writes_into_pages_repo_and_returns_a_url(tmp_path, monkeypatch):
    jp = _journal(tmp_path)
    out = str(tmp_path / "r.json")
    _run(monkeypatch, jp, "--claim", "shipped healthz", "-o", out)     # mint a receipt
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setenv("KORGEX_SHARE_PAGES_REPO", str(site))
    monkeypatch.setenv("KORGEX_SHARE_BASE_URL", "https://yvaehkorg.lol")
    assert _run(monkeypatch, "share", out, "--publish") == 0           # write ok even if git push no-ops
    rec = json.loads(Path(out).read_text())
    page = site / "r" / f"{rec['tip'][:12]}.html"
    assert page.exists()
    assert "https://yvaehkorg.lol/r/" in page.read_text()              # public URL baked into the card


def test_share_publish_requires_config(tmp_path, monkeypatch):
    out = str(tmp_path / "r.json")
    _run(monkeypatch, _journal(tmp_path), "-o", out)
    monkeypatch.delenv("KORGEX_SHARE_PAGES_REPO", raising=False)
    monkeypatch.delenv("KORGEX_SHARE_BASE_URL", raising=False)
    assert _run(monkeypatch, "share", out, "--publish") == 2           # missing config → usage error


def test_argparse_accepts_both_receipt_forms():
    # The real CLI routes through this strict parser BEFORE cmd_receipt sees argv —
    # the two-token `verify <file>` form must not be rejected as an extra positional.
    p = cli._build_subcommand_parser()
    p.parse_args(["receipt", "verify", "/tmp/r.json"])                       # must not SystemExit
    p.parse_args(["receipt", "share", "/tmp/r.json", "-o", "/tmp/page.html"])  # share <file> form
    p.parse_args(["receipt", "j.jsonl", "--claim", "x", "--sign",
                  "-o", "o.json", "--html", "h.html"])
