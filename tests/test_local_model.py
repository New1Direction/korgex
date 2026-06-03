"""Local-model advisor — pure logic (the llmfit-backed `korgex local`).

korgex is hosted-provider-first; it has no notion of "what local model fits THIS
machine." `korgex local` shells out to llmfit (if present) for hardware-aware
recommendations, can wire the pick into config (local Ollama), and records the
choice to the verifiable ledger. These pin the pure pieces — runnable without
llmfit installed (the JSON is fixture data matching llmfit's documented `--json`
contract; the shell-out itself is a thin wrapper validated separately).
"""
from __future__ import annotations

from src import local_model as LM
from src.config import Config, provider_type_for_model

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
