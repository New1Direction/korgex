import json
import sys

from src import korg_ledger as KL
from src import near_anchor as NA
from src import receipt as RC
from src import signing


def _journal(tmp_path):
    path = tmp_path / "journal.jsonl"
    client = KL.LocalJournalClient(journal_path=str(path))
    root = client.record_user_prompt("fix the NEAR contract test")
    client.record_tool_call(
        "Edit",
        {"file_path": "contract/src/lib.rs", "old": "red", "new": "green"},
        {"ok": True},
        True,
        12,
        triggered_by=root,
    )
    return path


def test_near_anchor_from_journal_exposes_only_hashes_in_contract_args(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)

    anchor = NA.build_anchor_from_path(
        journal,
        account_id="alice.testnet",
        contract_id="korgex-anchor.testnet",
        memo="issue #123 fixed",
        repo=str(tmp_path),
    )

    events = KL.load_journal_raw(str(journal))
    args = anchor["contract_call"]["args"]
    assert anchor["schema"] == NA.SCHEMA
    assert args["ledger_root"] == events[-1]["entry_hash"]
    assert args["event_count"] == 2
    assert args["memo"] == "issue #123 fixed"
    assert args["receipt_sha256"] is None

    on_chain = json.dumps(args, sort_keys=True)
    assert "fix the NEAR contract test" not in on_chain
    assert "contract/src/lib.rs" not in on_chain
    assert "red" not in on_chain and "green" not in on_chain


def test_near_anchor_from_signed_receipt_links_receipt_hash(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)
    events = KL.load_journal_raw(str(journal))
    priv, _ = signing.generate_keypair()
    receipt = RC.build_receipt(events, claim="fixed", signer_priv=priv, generated_at=1.0)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))

    anchor = NA.build_anchor_from_path(receipt_path, repo=str(tmp_path))

    assert anchor["source"]["kind"] == "receipt"
    assert anchor["proof"]["receipt_sha256"]
    assert anchor["proof"]["signed_by"] == receipt["signature"]["pubkey"]
    assert anchor["contract_call"]["args"]["ledger_root"] == receipt["tip"]


def test_near_cli_command_uses_contract_account_network_and_method(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    anchor = NA.build_anchor_from_path(
        _journal(tmp_path),
        account_id="alice.testnet",
        contract_id="korgex-anchor.testnet",
        network="testnet",
        method_name="anchor",
        repo=str(tmp_path),
    )

    cmd = NA.near_cli_js_command(anchor)

    assert cmd.startswith("near call korgex-anchor.testnet anchor '")
    assert "--accountId alice.testnet" in cmd
    assert "--networkId testnet" in cmd
    assert anchor["proof"]["ledger_root"] in cmd


def test_cli_near_anchor_writes_payload(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)
    out = tmp_path / "anchor.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "korgex",
            "near",
            "anchor",
            str(journal),
            "--account",
            "alice.testnet",
            "--contract",
            "korgex-anchor.testnet",
            "--out",
            str(out),
            "--repo",
            str(tmp_path),
        ],
    )

    from src.cli import cmd_near

    assert cmd_near() == 0
    payload = json.loads(out.read_text())
    assert payload["schema"] == NA.SCHEMA
    assert payload["target"]["account_id"] == "alice.testnet"
    assert "near call korgex-anchor.testnet" in capsys.readouterr().out
