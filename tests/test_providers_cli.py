"""korgex providers CLI — register & select a self-hosted OpenAI-compatible endpoint.

`korgex providers add vllm --url … --model …` then `korgex providers use vllm` points
the whole agent at your own box (vLLM / llama.cpp / a gateway) in one command — a thin,
named layer over the existing KORGEX_API_URL routing, no env juggling.
"""
from src import cli
from src import config as C


def _run(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("KORGEX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr("sys.argv", ["korgex", "providers", *args])
    return cli.cmd_providers()


def test_providers_add_then_use_points_korgex_at_the_endpoint(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, "add", "vllm",
                "--url", "http://box:8000/v1", "--model", "my-ft") == 0
    assert _run(monkeypatch, tmp_path, "use", "vllm") == 0
    cfg = C.load_config(str(tmp_path / "config.json"))
    assert cfg.active_provider == "vllm"
    key, base = C.resolve_client_config("my-ft", cfg, env={})
    assert base == "http://box:8000/v1" and key == "EMPTY"


def test_providers_list_then_remove(tmp_path, monkeypatch):
    _run(monkeypatch, tmp_path, "add", "vllm", "--url", "u/v1", "--model", "m")
    assert _run(monkeypatch, tmp_path, "list") == 0
    assert _run(monkeypatch, tmp_path, "remove", "vllm") == 0
    assert C.load_config(str(tmp_path / "config.json")).provider_by_name("vllm") is None


def test_providers_add_requires_url_and_model(tmp_path, monkeypatch):
    assert _run(monkeypatch, tmp_path, "add", "vllm") == 2     # missing --url/--model → usage error
