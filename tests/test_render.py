"""Clean block-rendered output (inspired generically by accent-block TUIs).

Each message renders as a left-accent block (▎ + per-type color) with the content
beside it; tool calls render as a compact `◆ verb  target  (dim details)` line.
This replaces the raw-text 'slop'. Pure formatters here (return strings/markup);
the terminal paint is verified by eye.
"""
from src import render as R


# ── theme: semantic accent slots, no hardcoded colors at call sites ────────────

def test_theme_has_distinct_accents_per_role():
    t = R.Theme()
    roles = ["user", "assistant", "tool", "error", "success", "thinking"]
    colors = [t.accent(r) for r in roles]
    assert all(colors), "every role has an accent color"
    assert len(set(colors)) >= 4, "roles are visually distinguishable, not all one color"


def test_theme_unknown_role_falls_back():
    assert R.Theme().accent("nonsense")  # never crashes / empty


# ── block: left accent bar + content ───────────────────────────────────────────

def test_block_has_accent_bar_and_label():
    out = R.block("user", "add rate limiting", label="you")
    assert "▎" in out                 # the accent bar
    assert "you" in out               # the role label
    assert "add rate limiting" in out


def test_block_multiline_indents_each_line_under_the_bar():
    out = R.block("assistant", "line one\nline two", label="korgex")
    bars = [ln for ln in out.splitlines() if "▎" in ln]
    assert len(bars) >= 2, "every content line carries the accent bar (a real block, not one line)"


# ── tool call: compact ◆ verb target (dim details) ─────────────────────────────

def test_tool_line_is_compact_with_diamond_and_target():
    line = R.tool_line("Read", {"file_path": "api/routes.py"})
    assert "◆" in line
    assert "read" in line.lower()           # verb (from tool name)
    assert "api/routes.py" in line          # the target


def test_tool_line_picks_the_meaningful_arg_as_target():
    assert "limit.py" in R.tool_line("Edit", {"file_path": "limit.py", "old_string": "x"})
    assert "pytest" in R.tool_line("Bash", {"command": "pytest -q"})
    assert "handler" in R.tool_line("Grep", {"pattern": "handler"})


def test_tool_line_shows_edit_line_counts_as_dim_detail():
    line = R.tool_line("Edit", {"file_path": "f.py"}, detail="+12 −0")
    assert "+12" in line and "−0" in line


def test_tool_line_long_target_is_shortened():
    long = "/very/long/" + "deep/" * 20 + "file.py"
    line = R.tool_line("Read", {"file_path": long})
    assert len(line) < 200          # clamped, not a runaway line
    assert "file.py" in line        # keeps the meaningful tail


# ── truncation: long output shows head + tail with a collapsed marker ──────────

def test_short_output_is_untouched():
    text = "line1\nline2\nline3"
    assert R.truncate_output(text, first=2, last=3) == text  # ≤ first+last → as-is


def test_long_output_shows_head_tail_and_count():
    text = "\n".join(f"L{i}" for i in range(20))
    out = R.truncate_output(text, first=2, last=3)
    assert "L0" in out and "L1" in out          # head kept
    assert "L17" in out and "L19" in out        # tail kept
    assert "L9" not in out                       # middle dropped
    assert "15" in out and ("hidden" in out.lower() or "…" in out)  # collapsed-count marker


def test_thinking_truncates_to_n_lines():
    text = "\n".join(f"t{i}" for i in range(10))
    out = R.truncate_output(text, first=0, last=3)
    assert "t7" in out and "t9" in out and "t0" not in out


def test_truncate_handles_trailing_blank_lines():
    out = R.truncate_output("a\nb\n\n", first=1, last=1)
    assert "a" in out  # doesn't crash on trailing blanks


# ── streaming block: prefix each line with the accent bar as tokens arrive ─────

def test_streamblock_first_chunk_emits_label_and_bar():
    out = []
    sb = R.StreamBlock("assistant", label="korgex", sink=out.append)
    sb.feed("Hello")
    text = "".join(out)
    assert "korgex" in text and "▎" in text   # label + bar on the opening line
    assert "Hello" in text


def test_streamblock_newline_continues_the_bar():
    out = []
    sb = R.StreamBlock("assistant", label="korgex", sink=out.append)
    sb.feed("line one\nline two")
    text = "".join(out)
    # the second line also carries an accent bar (block continues, not a flat dump)
    assert text.count("▎") >= 2


def test_streamblock_close_is_idempotent_and_safe():
    out = []
    sb = R.StreamBlock("assistant", label="korgex", sink=out.append)
    sb.feed("hi")
    sb.close(); sb.close()  # no crash, no double trailing garbage


# ── input echo: the user's turn rendered as a ▎ you block ──────────────────────

def test_echo_user_renders_a_you_block():
    out = R.echo_user("add rate limiting")
    assert "▎" in out and "you" in out.lower() and "add rate limiting" in out


# ── markdown: final assistant prose rendered (headings/bold/code) ──────────────

def test_render_markdown_returns_ansi_with_content():
    s = R.render_markdown("# Title\n\nsome **bold** text")
    assert "Title" in s
    assert "bold" in s
    assert "\033[" in s  # actually rendered (ANSI escapes present)


def test_render_markdown_handles_code_block():
    s = R.render_markdown("here:\n```python\nprint('hi')\n```")
    assert "print" in s and "hi" in s


def test_render_markdown_empty_is_safe():
    assert R.render_markdown("") == "" or R.render_markdown("") is not None
