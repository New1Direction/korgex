"""Tests for the plugin loader (src/plugins.py load_plugins).

The hook registry already runs in-process observers; the loader is what makes
korgex *extensible by users* — drop a ``.py`` in ~/.korgex/plugins/ that defines
``register(registry)`` and its hooks run inside the agent loop. Fail-safe by
construction: a plugin that fails to import or register is recorded and skipped,
never crashing startup, and the others still load.
"""
import os

from src.plugins import PluginRegistry, default_plugin_dirs, load_plugins


def _write_plugin(d, name, body):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        f.write(body)


class TestDefaultPluginDirs:
    def test_includes_user_global_and_project(self, tmp_path):
        dirs = default_plugin_dirs(str(tmp_path / "repo"), home=str(tmp_path / "home"))
        assert os.path.join(str(tmp_path / "home"), ".korgex", "plugins") in dirs
        assert os.path.join(str(tmp_path / "repo"), ".korgex", "plugins") in dirs


class TestLoadPlugins:
    def test_loads_a_plugin_and_registers_its_hook(self, tmp_path):
        d = str(tmp_path / "plugins")
        _write_plugin(d, "auditor.py",
                      "def register(reg):\n"
                      "    reg.on('post_tool')(lambda payload: None)\n")
        reg = PluginRegistry()
        loaded = load_plugins(reg, [d])
        assert reg.count("post_tool") == 1
        assert any(p["name"] == "auditor" and p["ok"] for p in loaded)

    def test_a_broken_plugin_is_skipped_not_fatal(self, tmp_path):
        d = str(tmp_path / "plugins")
        _write_plugin(d, "good.py",
                      "def register(reg):\n    reg.on('pre_tool')(lambda c: None)\n")
        _write_plugin(d, "broken.py", "raise RuntimeError('boom on import')\n")
        reg = PluginRegistry()
        loaded = load_plugins(reg, [d])
        # the good one still registered; the broken one is recorded as failed
        assert reg.count("pre_tool") == 1
        assert any(p["name"] == "broken" and not p["ok"] for p in loaded)
        assert any(p["name"] == "good" and p["ok"] for p in loaded)

    def test_plugin_without_register_is_skipped(self, tmp_path):
        d = str(tmp_path / "plugins")
        _write_plugin(d, "noreg.py", "x = 1\n")   # no register()
        reg = PluginRegistry()
        loaded = load_plugins(reg, [d])
        assert any(p["name"] == "noreg" and not p["ok"] for p in loaded)
        assert reg.count() == 0

    def test_dunder_and_underscore_files_are_ignored(self, tmp_path):
        d = str(tmp_path / "plugins")
        _write_plugin(d, "__init__.py", "raise RuntimeError('should not import')\n")
        _write_plugin(d, "_helper.py", "raise RuntimeError('should not import')\n")
        reg = PluginRegistry()
        loaded = load_plugins(reg, [d])
        assert loaded == []          # neither was even considered

    def test_missing_dir_is_a_noop(self):
        reg = PluginRegistry()
        assert load_plugins(reg, ["/no/such/dir/anywhere"]) == []

    def test_register_receiving_a_bad_hook_name_is_isolated(self, tmp_path):
        # A plugin that tries to register on a nonexistent hook fails cleanly.
        d = str(tmp_path / "plugins")
        _write_plugin(d, "bad_hook.py",
                      "def register(reg):\n    reg.register('not_a_hook', lambda *a: None)\n")
        reg = PluginRegistry()
        loaded = load_plugins(reg, [d])
        assert any(p["name"] == "bad_hook" and not p["ok"] for p in loaded)
