"""Stale-file detection — refuse to edit a file that changed on disk since the
agent last read it (prevents clobbering external/out-of-band changes). Baseline is
a content hash, recorded on Read and refreshed after our own writes (so korgex's
own edits never falsely read as stale)."""
from src import edit_freshness as EF


def test_recorded_read_is_fresh(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\n")
    EF.record_read(str(p))
    assert EF.check_fresh(str(p))[0] == "ok"


def test_external_change_after_read_is_stale(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\n")
    EF.record_read(str(p))
    p.write_text("x = 999\n")          # changed out-of-band after the read
    status, reason = EF.check_fresh(str(p))
    assert status == "stale" and reason


def test_unread_existing_file_is_unknown(tmp_path):
    p = tmp_path / "b.py"
    p.write_text("y = 2\n")
    assert EF.check_fresh(str(p))[0] == "unknown"   # no baseline → can't claim stale


def test_missing_file_is_new(tmp_path):
    assert EF.check_fresh(str(tmp_path / "nope.py"))[0] == "new"


def test_recording_after_write_clears_staleness(tmp_path):
    # korgex's OWN write must update the baseline so the next edit isn't false-stale
    p = tmp_path / "a.py"
    p.write_text("x = 1\n")
    EF.record_read(str(p))
    p.write_text("x = 2\n")             # simulate korgex writing
    EF.record_read(str(p))             # ...and refreshing the baseline
    assert EF.check_fresh(str(p))[0] == "ok"
