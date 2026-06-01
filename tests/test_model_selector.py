"""Model selector — pick by number from a priced menu; cheap by default.

The setup wizard was defaulting to the most expensive model (opus). Fix: a
priced menu (so you choose cost-aware), a CHEAP default, and a numbered selector
so `/model` lets you pick instead of only listing. Pure selection logic here.
"""
from src import model_selector as MS


def test_catalog_has_priced_tiers_for_a_provider():
    rows = MS.menu_for("anthropic")
    assert rows, "anthropic should offer suggestions"
    # each row carries a model id + a human price/cost hint
    r = rows[0]
    assert r["model"] and r["label"]
    assert any("$" in r["label"] or "free" in r["label"].lower() or "cheap" in r["label"].lower()
               for r in rows), "menu should surface cost so you choose with eyes open"


def test_default_is_not_the_most_expensive(tmp_path):
    # The connect-time default must be a mid/cheap tier, never opus/most-expensive.
    d = MS.default_model_for("anthropic")
    assert "opus" not in d.lower(), f"default must not be the priciest model, got {d}"


def test_default_for_openrouter_is_cheap_too():
    d = MS.default_model_for("openrouter")
    assert "opus" not in d.lower()


def test_pick_by_number_returns_the_model():
    rows = MS.menu_for("anthropic")
    # 1-indexed selection like a real menu
    assert MS.pick(rows, "1") == rows[0]["model"]
    assert MS.pick(rows, str(len(rows))) == rows[-1]["model"]


def test_pick_freeform_id_passes_through():
    rows = MS.menu_for("anthropic")
    # a non-numeric answer is treated as a literal model id (no lock-in to the menu)
    assert MS.pick(rows, "some/custom-model") == "some/custom-model"


def test_pick_blank_returns_none():
    rows = MS.menu_for("anthropic")
    assert MS.pick(rows, "") is None
    assert MS.pick(rows, "   ") is None


def test_pick_out_of_range_number_is_treated_as_literal():
    rows = MS.menu_for("anthropic")
    # "99" isn't a row → treat as a literal id rather than crashing
    assert MS.pick(rows, "99") == "99"


def test_render_menu_is_numbered_and_priced():
    rows = MS.menu_for("anthropic")
    text = MS.render_menu(rows, current="claude-sonnet-4-6")
    assert "1." in text or "1)" in text          # numbered
    assert "claude" in text.lower()
    assert "current" in text.lower() or "→" in text  # marks the active model
