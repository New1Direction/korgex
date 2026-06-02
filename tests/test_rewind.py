"""Session rewind: undo file edits back to an earlier prompt.

Each turn, the first time a file is touched we record its START-of-turn content
(or None if it didn't exist). Rewinding "to turn N" restores every file to the
state it had when turn N began — restoring content, or deleting files that were
created at/after N. The restore computation is pure; the writer is injected.
"""
from src.rewind import (
    RewindLog,
    compute_restore,
    line_delta,
    render_change_summary,
    summarize_changes,
)


# ── post-turn change summary (built on the same per-turn snapshots) ──────────

class TestLineDelta:
    def test_replace_counts_both_sides(self):
        assert line_delta("a\nb\nc", "a\nX\nc") == (1, 1)

    def test_pure_insert(self):
        assert line_delta("a\nb", "a\nb\nc") == (1, 0)

    def test_pure_delete(self):
        assert line_delta("a\nb\nc", "a\nc") == (0, 1)

    def test_created_file_is_all_additions(self):
        assert line_delta(None, "a\nb") == (2, 0)

    def test_deleted_file_is_all_removals(self):
        assert line_delta("a\nb", None) == (0, 2)


class TestSummarizeChanges:
    def test_classifies_and_counts_each_file(self):
        records = [("new.py", None), ("edit.py", "a\nb\nc"), ("gone.py", "x\ny")]
        post = {"new.py": "a\nb", "edit.py": "a\nB\nc", "gone.py": None}
        items = summarize_changes(records, read_fn=lambda p: post[p])
        by = {it["path"]: it for it in items}
        assert by["new.py"]["kind"] == "created" and by["new.py"]["added"] == 2
        assert by["edit.py"] == {
            "path": "edit.py", "kind": "modified", "added": 1, "removed": 1}
        assert by["gone.py"]["kind"] == "deleted" and by["gone.py"]["removed"] == 2

    def test_unchanged_files_are_dropped(self):
        records = [("same.py", "a\nb")]
        items = summarize_changes(records, read_fn=lambda p: "a\nb")
        assert items == []   # no net change → not reported


class TestRenderChangeSummary:
    def test_empty_is_blank(self):
        assert render_change_summary([]) == ""

    def test_renders_counts_per_file(self):
        out = render_change_summary([
            {"path": "a.py", "kind": "modified", "added": 3, "removed": 1}])
        assert "a.py" in out and "+3" in out and "-1" in out


def test_record_pre_keeps_only_the_first_state_per_turn():
    log = RewindLog()
    log.record_pre(1, "a.py", "v0")
    log.record_pre(1, "a.py", "v1")   # same turn, second touch — ignored
    plan = log.plan_restore(1)
    assert plan == {"a.py": "v0"}     # start-of-turn-1 state, not the later one


def test_compute_restore_to_start_of_a_turn():
    records = [
        (1, "a.py", "A0"),     # a existed as A0 before turn 1 edited it
        (2, "a.py", "A1"),     # a was A1 before turn 2 edited it
        (2, "b.py", None),     # b did not exist before turn 2 created it
    ]
    # restore to start of turn 2: a→A1, b→delete
    assert compute_restore(records, 2) == {"a.py": "A1", "b.py": None}
    # restore to start of turn 1: a→A0, and b (first seen turn 2 ≥ 1) →delete
    assert compute_restore(records, 1) == {"a.py": "A0", "b.py": None}


def test_points_lists_turns_with_prompts_in_order():
    log = RewindLog()
    log.begin_turn(1, "add feature")
    log.record_pre(1, "a.py", "x")
    log.begin_turn(2, "fix bug")
    log.record_pre(2, "b.py", None)
    pts = log.points()
    assert [(p.turn, p.prompt) for p in pts] == [(1, "add feature"), (2, "fix bug")]


def test_restore_applies_via_injected_writer():
    log = RewindLog()
    log.record_pre(1, "a.py", "ORIG")
    log.record_pre(2, "new.py", None)
    done = []

    def writer(path, content):
        done.append((path, "delete" if content is None else f"write:{content}"))
        return "ok"

    actions = log.restore(1, writer=writer)
    d = dict(done)
    assert d["a.py"] == "write:ORIG"
    assert d["new.py"] == "delete"
    assert len(actions) == 2


def test_repl_rewind_command_restores_files(tmp_path):
    # End-to-end through the REPL handler: /rewind 1 restores the recorded files.
    import io

    from src.repl import Repl, parse_repl_input
    f = tmp_path / "x.py"
    f.write_text("EDITED BY AGENT")
    r = Repl(out=io.StringIO())
    r._rewind = RewindLog()
    r._rewind.begin_turn(1, "change x")
    r._rewind.record_pre(1, str(f), "ORIGINAL")

    r.handle(parse_repl_input("/rewind 1"))

    assert f.read_text() == "ORIGINAL"


def test_restore_real_files(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("EDITED")          # current (post-edit) state
    created = tmp_path / "new.py"
    created.write_text("created this turn")
    log = RewindLog()
    log.record_pre(1, str(a), "ORIGINAL")   # a was ORIGINAL before turn 1
    log.record_pre(1, str(created), None)    # new.py didn't exist before turn 1

    log.restore(1)

    assert a.read_text() == "ORIGINAL"       # restored
    assert not created.exists()              # creation undone
