"""korgex ships a baseline skill library (the common SWE skills every frontier
agent bundles), discovered from a built-in root with the lowest precedence so a
user/project skill of the same name overrides it."""
from src import skills as SK


def test_builtin_skills_are_bundled_and_loaded():
    reg = SK.load_skills(SK.default_skill_roots(None))
    names = reg.names()
    for expected in (
        # first wave
        "test-driven-development", "systematic-debugging", "writing-a-plan",
        "code-review", "verify-before-done", "delegating-to-subagents",
        "web-research", "authoring-a-skill",
        # second wave
        "safe-refactoring", "using-git", "exploring-a-codebase",
        "requesting-code-review", "handling-ambiguity", "writing-clearly",
        "spike", "condition-based-waiting",
        # third wave — core SWE depth + delivery
        "performance-profiling", "security-review", "error-handling",
        "concurrency-correctness", "managing-dependencies", "api-design",
        "database-migrations", "observability-and-logging",
        # fourth wave — dev/ops + MCP (generic, from the surveyed inventory)
        "building-an-mcp-server", "issue-triage", "ci-cd-pipelines",
        "deploying-safely", "incident-response", "designing-a-job-queue",
        "data-pipelines", "handling-webhooks",
    ):
        assert expected in names, f"{expected} missing from built-in library: {names}"
    assert len(names) >= 32


def test_builtin_skills_are_marked_built_in():
    reg = SK.load_skills(SK.default_skill_roots(None))
    sk = reg.get("test-driven-development")
    assert sk is not None and sk.trust == "built-in"
    assert sk.description and sk.body  # has a usable body


def test_builtin_root_has_lowest_precedence(tmp_path):
    # a user/project skill with a built-in's name shadows the built-in
    import os
    d = tmp_path / "tdd"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: test-driven-development\ndescription: my override\ntrust: user\n---\nmine\n")
    roots = SK.default_skill_roots(None) + [str(tmp_path)]
    reg = SK.load_skills(roots)
    assert reg.get("test-driven-development").description == "my override"
