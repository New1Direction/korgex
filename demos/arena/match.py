"""Two agents play tic-tac-toe over the REAL korg bus — hands-free.

Each turn an agent reconstructs the board from the shared bus journal (i.e. it
literally reads the opponent's move off the verifiable channel), decides its
move with a small engine, and sends `play N · <taunt>` back over the bus. Every
move is therefore a hash-chained, tamper-evident korg-ledger@v1 event.

The "brain" is a deterministic engine here so the demo is reproducible with zero
API keys and always yields a decisive game — swap `korgex_move` / `codex_move`
for real LLM calls to make the reasoning genuine. korgex (X) plays a fork
strategy; codex (O) blocks direct threats but isn't fork-aware, so it gets forked.
"""
from __future__ import annotations

import sys
from pathlib import Path

# import the real bus from the korgex package
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from src import bus  # noqa: E402

WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
CORNERS, SIDES = (0, 2, 6, 8), (1, 3, 5, 7)
TAUNTS = ["i'll go easy on you", "no you won't", "i'm building something", "not so fast",
          "you can only block one", "…it's all i've got", "gg 🏆", "nice try", "too slow"]


def winner(b):
    for a, c, d in WINS:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a]
    return None


def legal(b):
    return [i for i in range(9) if not b[i]]


def _completing(b, me):
    for i in legal(b):
        b2 = b[:]; b2[i] = me
        if winner(b2) == me:
            return i
    return None


def _threats(b, me):
    n = 0
    for a, c, d in WINS:
        line = (b[a], b[c], b[d])
        if line.count(me) == 2 and line.count("") == 1:
            n += 1
    return n


def _fork(b, me):
    for i in legal(b):
        b2 = b[:]; b2[i] = me
        if _threats(b2, me) >= 2:
            return i
    return None


# korgex plays strong (centre → corners → sides); codex plays weak (sides first,
# shuns the centre), a classic intermediate blunder that walks into a fork.
_STRONG = (4, 0, 2, 6, 8, 1, 3, 5, 7)
_WEAK = (1, 3, 5, 7, 0, 2, 6, 8, 4)


def _positional(b, order):
    for i in order:
        if i in legal(b):
            return i, ("center" if i == 4 else "corner" if i in CORNERS else "side")
    return legal(b)[0], "side"


def korgex_move(b):
    """X: win > make fork > block win > block fork > strong positional."""
    if (i := _completing(b, "X")) is not None:
        return i, "win"
    if (i := _fork(b, "X")) is not None:
        return i, "fork"
    if (i := _completing(b, "O")) is not None:
        return i, "block"
    if (i := _fork(b, "O")) is not None:
        return i, "blockfork"
    return _positional(b, _STRONG)


def codex_move(b):
    """O: win > block win > weak positional. Not fork-aware (so it gets forked)."""
    if (i := _completing(b, "O")) is not None:
        return i, "win"
    if (i := _completing(b, "X")) is not None:
        return i, "block"
    return _positional(b, _WEAK)


def _reason(label, cell):
    n = cell + 1
    return {
        "center": ["board's open.", "center is the only reply ◎"],
        "corner": ["nothing forced yet.", "anchor a corner ↘"],
        "side": ["keep it flexible.", "take a side"],
        "fork": ["they only block direct threats.", f"fork at {n} — two ways to win"],
        "win": ["the other threat is open.", f"{n} closes it · gg"],
        "block": ["they threaten a line.", f"block at {n}"],
        "blockfork": ["that's a fork forming.", f"cut it off at {n}"],
    }[label]


def board_from_bus(journal_path):
    """Reconstruct the board purely from the shared bus journal — this is the
    agent reading the opponent's move off the verifiable channel."""
    b = [""] * 9
    for m in bus.history(journal_path):
        body = m["body"]
        if not body.startswith("play "):
            continue
        cell = int(body.split()[1]) - 1
        b[cell] = "X" if m["from"] == "korgex" else "O"
    return b


def play_match(journal_path):
    """Run a full hands-free match over the real bus. Returns (moves, winner)."""
    turn, n, moves = "korgex", 0, []
    while True:
        b = board_from_bus(journal_path)        # read the shared channel
        if winner(b) is not None or not legal(b):
            break
        me = "X" if turn == "korgex" else "O"
        cell, label = (korgex_move if turn == "korgex" else codex_move)(b)
        taunt = TAUNTS[n % len(TAUNTS)]
        other = "codex" if turn == "korgex" else "korgex"
        seq = bus.send(journal_path, turn, other, f"play {cell + 1} · {taunt}")
        moves.append({"who": turn, "cell": cell, "mark": me, "seq": seq,
                      "reason": _reason(label, cell), "taunt": taunt, "label": label})
        turn = other
        n += 1
    return moves, winner(board_from_bus(journal_path))


if __name__ == "__main__":
    import json
    import os
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/arena_match.jsonl"
    if os.path.exists(out):
        os.remove(out)
    mv, w = play_match(out)
    events = [json.loads(line) for line in open(out) if line.strip()]
    from src import ledger_spec as S
    assert S.verify_chain(events) == [], "journal must verify"
    print(json.dumps({"winner": w, "moves": mv, "tip": events[-1]["entry_hash"],
                      "events": events}, ensure_ascii=False))
