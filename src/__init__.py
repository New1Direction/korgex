#!/usr/bin/env python3
"""Korgex — Autonomous AI Software Engineer
Jules spelled backwards. A a background task runner' architecture.

Usage:
    python -m cli.main --help
    python -m cli.main "fix the bug in main.py" --repo /path/to/repo
    python -m cli.main --schemas          # Print all 30+ tool schemas
    python -m cli.main --init             # Create AGENTS.md in repo

Environment:
    KORGEX_API_URL      LLM API endpoint (default: NousResearch)
    KORGEX_API_KEY      API key
    KORGEX_MODEL        Model name (default: deepseek/deepseek-v4-flash)
    KORGEX_PROVIDER     Provider name (default: nous)
    KORGEX_MAX_ITERATIONS  Max tool calls per task (default: 50)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import KorgexAgent, print_tool_schemas

try:  # single source of truth is pyproject.toml; read it via package metadata
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("korgex")
except Exception:
    __version__ = "0.0.0+dev"
__all__ = ["KorgexAgent", "print_tool_schemas"]