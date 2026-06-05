"""Local-model advisor — pure logic (the llmfit-backed `korgex local`).

korgex is hosted-provider-first; it has no notion of "what local model fits THIS
machine." `korgex local` shells out to llmfit (if present) for hardware-aware
recommendations, can wire the pick into config (local Ollama), and records the
choice to the verifiable ledger. These pin the pure pieces — runnable without
llmfit installed (the JSON is fixture data matching llmfit's documented `--json`
contract; the shell-out itself is a thin wrapper validated separately).
"""
from __future__ import annotations

import json

from src import local_model as LM
from src.config import Config, provider_type_for_model, resolve_client_config

# omlx GET /v1/models  (OpenAI-compatible; ids are the served model names)
OMLX_MODELS = {"data": [
    {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit", "owned_by": "omlx", "max_model_len": 131072},
    {"id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", "owned_by": "omlx"},
]}

# llmfit --json system  (documented schema)
SYS = {"system": {
    "cpu_name": "Apple M2", "cpu_cores": 8, "total_ram_gb": 16.0,
    "available_ram_gb": 10.0, "has_gpu": True, "gpu_name": "Apple M2 GPU",
    "gpu_vram_gb": 16.0, "gpu_count": 1, "backend": "metal", "unified_memory": True}}

# llmfit recommend --json  (documented: models[] with name/best_quant/estimated_tps/…)
RECS = {"models": [
    {"name": "Qwen/Qwen2.5-Coder-7B-Instruct", "provider": "qwen", "params_b": 7.6,
     "score": 88, "score_components": {"quality": 90, "speed": 80, "fit": 95},
     "fit_level": "Good", "run_mode": "GPU", "is_moe": False, "best_quant": "Q5_K_M",
     "estimated_tps": 42.0, "memory_required_gb": 6.5, "context_length": 32768,
     "notes": "fast on this box"},
    {"name": "deepseek-ai/deepseek-coder-1.3b", "best_quant": "Q4_K_M",
     "fit_level": "Perfect", "run_mode": "GPU", "estimated_tps": 110.0,
     "memory_required_gb": 1.2, "score": 74},
]}


def test_parse_system():
    s = LM.parse_system(SYS)
    assert s["cpu"] == "Apple M2" and s["cores"] == 8
    assert s["ram_gb"] == 16.0 and s["vram_gb"] == 16.0
    assert s["gpu"] == "Apple M2 GPU" and s["unified"] is True


def test_parse_system_tolerates_missing():
    assert LM.parse_system({}) is not None
    assert LM.parse_system({"system": {}})["cpu"] is None
    # has_gpu False → no gpu name surfaced
    assert LM.parse_system({"system": {"has_gpu": False, "gpu_name": "x"}})["gpu"] is None


def test_parse_recommendations():
    recs = LM.parse_recommendations(RECS)
    assert len(recs) == 2
    top = recs[0]
    assert top["name"] == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert top["quant"] == "Q5_K_M" and top["tps"] == 42.0
    assert top["fit"] == "Good" and top["run_mode"] == "GPU"


def test_parse_recommendations_tolerates_garbage():
    assert LM.parse_recommendations({}) == []
    assert LM.parse_recommendations({"models": "nope"}) == []
    assert LM.parse_recommendations({"models": [1, {"name": "ok"}]})[0]["name"] == "ok"


def test_provider_type_recognizes_ollama():
    # REGRESSION: an "ollama/<tag>" id used to fall into the "/" → openrouter branch,
    # so a wired local model never routed to the ollama provider.
    assert provider_type_for_model("ollama/qwen2.5-coder:7b") == "ollama"
    assert provider_type_for_model("ollama/llama3.3") == "ollama"
    # existing routing is unchanged
    assert provider_type_for_model("gpt-4o") == "openai"
    assert provider_type_for_model("claude-sonnet-4-6") == "anthropic"
    assert provider_type_for_model("meta/llama-3.1") == "openrouter"


def test_normalize_ollama_model():
    assert LM.normalize_ollama_model("qwen2.5-coder:7b") == "ollama/qwen2.5-coder:7b"
    assert LM.normalize_ollama_model("ollama/qwen2.5-coder:7b") == "ollama/qwen2.5-coder:7b"
    assert LM.normalize_ollama_model("  llama3.3 ") == "ollama/llama3.3"


def test_set_local_model_sets_default_and_ensures_ollama_provider():
    cfg = Config(default_model="openai/gpt-4o", providers=[{"type": "openai", "api_key": "k"}])
    LM.set_local_model(cfg, "qwen2.5-coder:7b")
    assert cfg.default_model == "ollama/qwen2.5-coder:7b"
    types = [p.get("type") for p in cfg.providers]
    assert "ollama" in types and "openai" in types          # added, kept the existing
    # idempotent: setting again doesn't duplicate the ollama provider
    LM.set_local_model(cfg, "llama3.3")
    assert [p.get("type") for p in cfg.providers].count("ollama") == 1
    assert cfg.default_model == "ollama/llama3.3"


# ── omlx local backend (Apple-Silicon MLX server; OpenAI/Anthropic compatible) ──

def test_clean_omlx_model_strips_stray_prefix():
    assert LM.clean_omlx_model("mlx-community/Llama-3.2-3B-Instruct-4bit") == "mlx-community/Llama-3.2-3B-Instruct-4bit"
    assert LM.clean_omlx_model("omlx/mlx-community/Foo") == "mlx-community/Foo"  # user-typed prefix dropped
    assert LM.clean_omlx_model("  spaced  ") == "spaced"


def test_set_omlx_model_wires_named_active_provider():
    cfg = Config(default_model="claude-sonnet-4-6", providers=[{"type": "anthropic", "api_key": "k"}])
    cfg = LM.set_omlx_model(cfg, "mlx-community/Llama-3.2-3B-Instruct-4bit", "http://localhost:8000/v1")
    # default points at the REAL omlx model id (no prefix — it goes on the wire verbatim)
    assert cfg.default_model == "mlx-community/Llama-3.2-3B-Instruct-4bit"
    p = cfg.provider_by_name("omlx")
    assert p and p["type"] == "openai" and p["base_url"] == "http://localhost:8000/v1"
    assert cfg.active_provider == "omlx"
    # the pre-existing anthropic provider is preserved
    assert any(x.get("type") == "anthropic" for x in cfg.providers)


def test_set_omlx_model_is_idempotent():
    cfg = Config()
    cfg = LM.set_omlx_model(cfg, "model-a", "http://localhost:8000/v1")
    cfg = LM.set_omlx_model(cfg, "model-b", "http://localhost:9000/v1")
    omlx = [p for p in cfg.providers if p.get("name") == "omlx"]
    assert len(omlx) == 1                                   # replaced, not duplicated
    assert omlx[0]["model"] == "model-b" and omlx[0]["base_url"] == "http://localhost:9000/v1"
    assert cfg.default_model == "model-b"


def test_resolve_client_config_routes_a_wired_omlx_model_to_its_endpoint():
    # the integration guarantee: a wired omlx model resolves to omlx's base_url over
    # the OpenAI-compatible transport, with no real key required.
    cfg = LM.set_omlx_model(Config(), "mlx-community/Llama-3.2-3B-Instruct-4bit", "http://localhost:8000/v1")
    key, base = resolve_client_config(cfg.default_model, cfg, env={})
    assert base == "http://localhost:8000/v1"
    assert key  # non-None placeholder so the OpenAI client builds


def test_detect_omlx_parses_served_model_ids():
    served = LM.detect_omlx("http://localhost:8000/v1", fetch=lambda url: json.dumps(OMLX_MODELS))
    assert served == ["mlx-community/Llama-3.2-3B-Instruct-4bit",
                      "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"]


def test_detect_omlx_hits_the_models_endpoint():
    seen = {}
    def fetch(url):
        seen["url"] = url
        return json.dumps(OMLX_MODELS)
    LM.detect_omlx("http://localhost:8000/v1", fetch=fetch)
    assert seen["url"] == "http://localhost:8000/v1/models"


def test_detect_omlx_returns_none_when_unreachable():
    def boom(url):
        raise OSError("connection refused")
    assert LM.detect_omlx("http://localhost:8000/v1", fetch=boom) is None


def test_detect_omlx_returns_none_for_non_omlx_response():
    assert LM.detect_omlx("http://localhost:8000/v1", fetch=lambda url: "not json") is None
    assert LM.detect_omlx("http://localhost:8000/v1", fetch=lambda url: json.dumps({"nope": 1})) is None


def test_detect_omlx_empty_when_up_but_no_models():
    served = LM.detect_omlx("http://localhost:8000/v1", fetch=lambda url: json.dumps({"data": []}))
    assert served == []   # up-but-empty ([]) is distinct from down (None)


def test_detect_omlx_does_not_swallow_programming_errors():
    # the catch is deliberately narrow (I/O + decode only): a genuine bug must surface,
    # not silently degrade to "omlx down". Guards against re-widening to `except Exception`.
    import pytest
    def buggy(url):
        raise TypeError("oops, a real bug")
    with pytest.raises(TypeError):
        LM.detect_omlx("http://localhost:8000/v1", fetch=buggy)


def test_llmfit_available(monkeypatch):
    monkeypatch.setattr(LM.shutil, "which", lambda n: None)
    assert LM.llmfit_available() is False
    monkeypatch.setattr(LM.shutil, "which", lambda n: "/usr/local/bin/llmfit")
    assert LM.llmfit_available() is True


def test_format_advice_smoke():
    out = LM.format_advice(LM.parse_recommendations(RECS), LM.parse_system(SYS))
    assert "Qwen2.5-Coder-7B-Instruct" in out
    assert "Q5_K_M" in out and "42" in out          # quant + tok/s surfaced
    assert "Apple M2" in out                          # hardware line


# ── the CLI command (config/journal redirected so the real ones are untouched) ─

def test_cmd_local_use_wires_config(tmp_path, monkeypatch):
    from src import cli
    from src import config as C
    cfgpath = tmp_path / "config.json"
    monkeypatch.setenv("KORGEX_CONFIG", str(cfgpath))
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "j.jsonl"))
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "local", "--use", "qwen2.5-coder:7b"])
    assert cli.cmd_local() == 0
    cfg = C.load_config(str(cfgpath))
    assert cfg.default_model == "ollama/qwen2.5-coder:7b"
    assert any(p.get("type") == "ollama" for p in cfg.providers)


def test_cmd_local_advise_degrades_without_llmfit(tmp_path, monkeypatch, capsys):
    from src import cli
    monkeypatch.setattr(LM.shutil, "which", lambda n: None)  # llmfit absent
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "local"])
    rc = cli.cmd_local()
    out = capsys.readouterr().out.lower()
    assert rc == 1 and "llmfit" in out          # helpful message, no crash


def test_cmd_local_omlx_use_wires_named_active_provider(tmp_path, monkeypatch):
    from src import cli
    from src import config as C
    cfgpath = tmp_path / "config.json"
    monkeypatch.setenv("KORGEX_CONFIG", str(cfgpath))
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "j.jsonl"))
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.setattr(cli.sys, "argv",
                        ["korgex", "local", "--omlx", "--use", "mlx-community/Llama-3.2-3B-Instruct-4bit"])
    assert cli.cmd_local() == 0
    cfg = C.load_config(str(cfgpath))
    assert cfg.default_model == "mlx-community/Llama-3.2-3B-Instruct-4bit"
    assert cfg.active_provider == "omlx"
    p = cfg.provider_by_name("omlx")
    assert p and p["base_url"] == "http://localhost:8000/v1"


def test_cmd_local_omlx_honors_custom_url(tmp_path, monkeypatch):
    from src import cli
    from src import config as C
    cfgpath = tmp_path / "config.json"
    monkeypatch.setenv("KORGEX_CONFIG", str(cfgpath))
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "j.jsonl"))
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.setattr(cli.sys, "argv",
                        ["korgex", "local", "--omlx", "--omlx-url", "http://localhost:9001/v1", "--use", "foo"])
    assert cli.cmd_local() == 0
    cfg = C.load_config(str(cfgpath))
    assert cfg.provider_by_name("omlx")["base_url"] == "http://localhost:9001/v1"


def test_cmd_local_omlx_lists_served_models(monkeypatch, capsys):
    from src import cli
    monkeypatch.setattr(LM, "detect_omlx", lambda *a, **k: ["mlx-community/Llama-3.2-3B-Instruct-4bit"])
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "local", "--omlx"])
    rc = cli.cmd_local()
    out = capsys.readouterr().out
    assert rc == 0
    assert "mlx-community/Llama-3.2-3B-Instruct-4bit" in out
    assert "--use" in out                       # tells the user how to wire it


def test_cmd_local_omlx_degrades_when_server_down(monkeypatch, capsys):
    from src import cli
    monkeypatch.setattr(LM, "detect_omlx", lambda *a, **k: None)   # not reachable
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "local", "--omlx"])
    rc = cli.cmd_local()
    out = capsys.readouterr().out.lower()
    assert rc == 1 and "omlx" in out            # helpful message, no crash
