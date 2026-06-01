"""Startup banner — a designed boot screen (wordmark + status line), not one bare line.

The REPL opened with a single plain line. Real agent CLIs open with a wordmark +
a status line (model, cwd, version) + a hint. These pin the pure assembly; the
rich rendering is a thin shell over it.
"""
from src import banner as B


def test_wordmark_is_multiline_ascii():
    art = B.wordmark()
    assert isinstance(art, str)
    assert art.count("\n") >= 2  # a real multi-row wordmark, not one line


def test_status_line_shows_model_and_version():
    line = B.status_line(model="claude-sonnet-4-6", cwd="/Users/x/proj", version="0.10.0")
    assert "claude-sonnet-4-6" in line
    assert "0.10.0" in line


def test_status_line_shortens_home_to_tilde():
    import os
    home = os.path.expanduser("~")
    line = B.status_line(model="m", cwd=os.path.join(home, "proj"), version="1")
    assert "~/proj" in line and home not in line


def test_hint_line_lists_core_commands():
    h = B.hint_line()
    assert "/help" in h and "/exit" in h


def test_startup_text_assembles_all_parts():
    text = B.startup_text(model="gpt-4o", cwd="/tmp/p", version="9.9.9",
                          configured=True)
    assert "gpt-4o" in text and "9.9.9" in text
    # the wordmark's letters/box-chars are present (designed, not a bare line)
    assert text.count("\n") >= 4


def test_startup_text_unconfigured_prompts_setup():
    text = B.startup_text(model="m", cwd="/tmp", version="1", configured=False)
    assert "setup" in text.lower()  # nudges `korgex setup` when no provider yet


# ── welcome dashboard: fills the space with skills / MCPs / providers / tips ───

def test_dashboard_lists_providers_and_model():
    text = B.dashboard(model="gpt-4o", cwd="/tmp/p", version="1",
                       providers=["openrouter"], skills=[], mcps=[])
    assert "gpt-4o" in text
    assert "openrouter" in text


def test_dashboard_shows_skills_when_present():
    text = B.dashboard(model="m", cwd="/p", version="1", providers=["anthropic"],
                       skills=[("fix-flaky", "find and fix flaky tests")], mcps=[])
    assert "fix-flaky" in text and "flaky tests" in text


def test_dashboard_shows_mcps_when_present():
    text = B.dashboard(model="m", cwd="/p", version="1", providers=["anthropic"],
                       skills=[], mcps=["github", "filesystem"])
    assert "github" in text and "filesystem" in text


def test_dashboard_gives_starter_hint_when_empty():
    # No skills, no MCPs → don't show empty sections; nudge how to add them.
    text = B.dashboard(model="m", cwd="/p", version="1", providers=["anthropic"],
                       skills=[], mcps=[])
    assert "skill" in text.lower() or "tip" in text.lower()  # a helpful nudge, not blank


def test_dashboard_always_has_quick_tips():
    text = B.dashboard(model="m", cwd="/p", version="1", providers=["anthropic"],
                       skills=[], mcps=[])
    # at least one actionable starter tip (a slash command or "ask me to…")
    assert "/" in text


# ── upgraded look: gradient wordmark + paneled welcome + summary footer ────────

def test_wordmark_has_box_drawing_block_letters():
    art = B.wordmark()
    # uses heavy box-drawing block chars (the 3D look), not plain ██ only
    assert any(c in art for c in "╗╔╝╚║═█")


def test_summary_line_counts_skills_mcps():
    s = B.summary_line(skills=5, mcps=3, tools=12)
    assert "5 skills" in s and "3 MCP" in s and ("12 tools" in s or "12 tool" in s)


def test_categorize_groups_skills_by_prefix():
    # "github-auth", "github-codegen" → grouped under "github"
    groups = B.categorize_skills([
        ("github-auth", "x"), ("github-codegen", "y"), ("apple-notes", "z"),
    ])
    assert "github" in groups and set(groups["github"]) == {"github-auth", "github-codegen"}
    assert groups["apple"] == ["apple-notes"]


def test_categorize_uncategorized_goes_to_general():
    groups = B.categorize_skills([("deploy", "ship it")])
    # a bare name with no prefix lands in a catch-all bucket, not lost
    assert any("deploy" in v for v in groups.values())


# ── centered reddish-gradient wordmark + style setting ─────────────────────────

def test_wordmark_tiers_red_palette_exists():
    tiers = B.wordmark_tiers("red")
    assert len(tiers) >= 4
    # reddish: red channel dominant in every tier
    for hexc in tiers:
        r = int(hexc[1:3], 16); g = int(hexc[3:5], 16); b = int(hexc[5:7], 16)
        assert r > g and r > b, f"{hexc} should be reddish"


def test_wordmark_tiers_gold_still_available():
    assert B.wordmark_tiers("gold")  # the original palette still selectable


def test_center_block_pads_to_width():
    centered = B.center_block("abc", width=11)
    # each line padded so content sits in the middle (leading spaces present)
    assert centered.startswith("    ") and "abc" in centered
