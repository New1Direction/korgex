"""Local-model advisor — the engine behind `korgex local`.

korgex is hosted-provider-first; it has no notion of which LOCAL model actually fits
the machine. This wraps **llmfit** (https://github.com/AlexsJones/llmfit) — IF it's
on PATH — to turn detected hardware into a ranked, fit-scored recommendation, wire
the pick into config (local Ollama, which is OpenAI-compatible), and record the
choice to the verifiable ledger. llmfit is OPTIONAL: absent → a helpful message,
never a crash; we never bundle or depend on its binary.

Parsers track llmfit's documented `--json` contract:
  - ``llmfit --json system`` → ``{"system": {cpu_name, cpu_cores, total_ram_gb,
    has_gpu, gpu_name, gpu_vram_gb, unified_memory, backend, …}}``
  - ``llmfit recommend --json`` → ``{"models": [{name, best_quant, estimated_tps,
    fit_level, run_mode, score, memory_required_gb, context_length, …}]}``

All pure + tolerant (missing/garbage fields never raise); the CLI orchestrates.
"""
from __future__ import annotations

import json
import shutil
import subprocess

OLLAMA_BASE_URL = "http://localhost:11434"


def llmfit_available() -> bool:
    """True iff the llmfit binary is on PATH (the optional dependency)."""
    return shutil.which("llmfit") is not None


def run_llmfit(args: list, timeout: int = 60):
    """Run ``llmfit <args>`` and parse its JSON stdout. Returns the parsed object,
    or None on ANY failure (missing binary, non-zero exit, bad JSON) — never raises,
    so a missing/odd llmfit degrades to "no advice" instead of crashing the CLI."""
    if not llmfit_available():
        return None
    try:
        proc = subprocess.run(["llmfit", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def parse_system(doc) -> dict:
    """Normalize ``llmfit --json system`` into a flat hardware summary."""
    s = (doc or {}).get("system") or {}
    return {
        "cpu": s.get("cpu_name"),
        "cores": s.get("cpu_cores"),
        "ram_gb": s.get("total_ram_gb"),
        "gpu": s.get("gpu_name") if s.get("has_gpu") else None,
        "vram_gb": s.get("gpu_vram_gb"),
        "unified": s.get("unified_memory"),
        "backend": s.get("backend"),
    }


def parse_recommendations(doc) -> list:
    """Normalize ``llmfit recommend --json`` into a ranked list of dicts. Skips any
    non-dict entries; returns [] for a missing/garbage ``models``."""
    models = (doc or {}).get("models")
    if not isinstance(models, list):
        return []
    out = []
    for m in models:
        if not isinstance(m, dict):
            continue
        out.append({
            "name": m.get("name"),
            "quant": m.get("best_quant"),
            "tps": m.get("estimated_tps"),
            "fit": m.get("fit_level"),
            "run_mode": m.get("run_mode"),
            "score": m.get("score"),
            "params_b": m.get("params_b"),
            "mem_gb": m.get("memory_required_gb"),
            "context": m.get("context_length"),
            "notes": m.get("notes"),
        })
    return out


def normalize_ollama_model(name: str) -> str:
    """Return the korgex model id for a local Ollama tag: ``ollama/<tag>`` (the
    prefix that routes to the ollama provider — see provider_type_for_model). Strips
    an existing ``ollama/`` so it never doubles."""
    n = (name or "").strip()
    if n.startswith("ollama/"):
        n = n[len("ollama/"):]
    return "ollama/" + n


def set_local_model(cfg, model: str, base_url: str = OLLAMA_BASE_URL):
    """Point korgex at a LOCAL Ollama model: set ``default_model`` to ``ollama/<tag>``
    and ensure the ollama provider exists (idempotent — never duplicates it). Mutates
    and returns ``cfg`` (the caller saves it)."""
    cfg.default_model = normalize_ollama_model(model)
    if not any((p or {}).get("type") == "ollama" for p in cfg.providers):
        cfg.providers.append({"type": "ollama", "base_url": base_url})
    return cfg


def format_advice(recs: list, system: dict) -> str:
    """Render a compact hardware line + a ranked recommendation table for the CLI."""
    lines = []
    hw = []
    if system.get("cpu"):
        hw.append(str(system["cpu"]))
    if isinstance(system.get("ram_gb"), (int, float)):
        hw.append(f"{system['ram_gb']:.0f}GB RAM")
    if system.get("gpu"):
        vram = system.get("vram_gb")
        vram_s = f"{vram:.0f}GB" if isinstance(vram, (int, float)) else "?GB"
        hw.append(f"{system['gpu']} {vram_s}")
    lines.append("  hardware: " + (" · ".join(hw) if hw else "unknown"))
    lines.append("")
    if not recs:
        lines.append("  (no recommendations)")
        return "\n".join(lines)
    lines.append(f"  {'model':42} {'quant':9} {'~tok/s':>7}  {'fit':9} {'run':14}")
    for r in recs:
        name = (r.get("name") or "?")[:42]
        quant = (r.get("quant") or "")[:9]
        tps = r.get("tps")
        tps_s = f"{tps:.0f}" if isinstance(tps, (int, float)) else "?"
        fit = (r.get("fit") or "")[:9]
        run = (r.get("run_mode") or "")[:14]
        lines.append(f"  {name:42} {quant:9} {tps_s:>7}  {fit:9} {run:14}")
    return "\n".join(lines)
