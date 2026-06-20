import os
import sys

from src import demo as DM


def test_near_omnigraph_demo_script_contains_full_flow():
    script = DM.near_omnigraph_script(DM.NearOmnigraphDemoOptions(
        near_account="alice.testnet",
        near_contract="korgex-anchor.alice.testnet",
        omnigraph_store="devgraph.omni",
        omnigraph_branch="agent/demo",
    ))

    assert script.startswith("#!/usr/bin/env bash")
    assert "korgex verify .korg/journal.jsonl" in script
    assert "korgex receipt .korg/journal.jsonl" in script
    assert "korgex omnigraph export" in script
    assert "omnigraph load --data" in script
    assert "--branch agent/demo" in script
    assert "korgex near anchor" in script
    assert "--account alice.testnet" in script
    assert "--contract korgex-anchor.alice.testnet" in script
    assert "cargo near build non-reproducible-wasm" in script


def test_cli_demo_writes_executable_script(tmp_path, monkeypatch, capsys):
    out = tmp_path / "demo.sh"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "korgex",
            "demo",
            "near-omnigraph",
            "--account",
            "alice.testnet",
            "--contract",
            "korgex-anchor.alice.testnet",
            "--write",
            str(out),
        ],
    )

    from src.cli import cmd_demo

    assert cmd_demo() == 0
    assert out.exists()
    assert os.access(out, os.X_OK)
    text = out.read_text()
    assert "alice.testnet" in text
    assert "korgex-anchor.alice.testnet" in text
    assert "demo script written" in capsys.readouterr().out


def test_near_anchor_contract_example_contains_expected_methods():
    src = "examples/near-anchor-contract/src/lib.rs"
    text = open(src, encoding="utf-8").read()
    assert "pub fn anchor(" in text
    assert "pub fn get_anchor(" in text
    assert "assert_hex_64" in text
    assert "env::predecessor_account_id()" in text
    assert "env::block_timestamp_ms()" in text
