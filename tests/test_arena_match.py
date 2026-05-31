"""The arena match-runner must produce a decisive game on a genuinely verifiable
korg-ledger@v1 journal, with the board reconstructable purely from the chain."""
import sys
from pathlib import Path

from src import ledger_spec as S

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demos" / "arena"))
import match as M  # noqa: E402


def test_match_is_decisive_and_korgex_wins(tmp_path):
    j = str(tmp_path / "m.jsonl")
    moves, w = M.play_match(j)
    assert w == "X", f"korgex (X) should win via the fork; got {w}"
    assert 5 <= len(moves) <= 9


def test_match_journal_is_a_valid_hash_chain(tmp_path):
    import json
    j = str(tmp_path / "m.jsonl")
    M.play_match(j)
    events = [json.loads(line) for line in open(j) if line.strip()]
    assert events, "match produced no events"
    assert S.verify_chain(events) == []                       # genuine korg-ledger@v1 chain
    tip = events[-1]["entry_hash"]
    assert S.verify_chain(events, expected_tip=tip) == []     # anchors cleanly


def test_board_reconstructs_from_the_bus_alone(tmp_path):
    j = str(tmp_path / "m.jsonl")
    moves, _ = M.play_match(j)
    board = M.board_from_bus(j)                                # rebuilt only from the journal
    for mv in moves:
        assert board[mv["cell"]] == mv["mark"]
    assert M.winner(board) == "X"
