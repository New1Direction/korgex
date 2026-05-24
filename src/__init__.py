#!/usr/bin/env python3
"""KorgKode — Autonomous AI Software Engineer
Jules spelled backwards. A a background task runner' architecture.

Usage:
    python -m cli.main --help
    python -m cli.main "fix the bug in main.py" --repo /path/to/repo
    python -m cli.main --schemas          # Print all 30+ tool schemas
    python -m cli.main --init             # Create AGENTS.md in repo

Environment:
    KORGKODE_API_URL      LLM API endpoint (default: NousResearch)
    KORGKODE_API_KEY      API key
    KORGKODE_MODEL        Model name (default: deepseek/deepseek-v4-flash)
    KORGKODE_PROVIDER     Provider name (default: nous)
    KORGKODE_MAX_ITERATIONS  Max tool calls per task (default: 50)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent import KorgKodeAgent, print_tool_schemas

__version__ = "2.0.0"
__all__ = ["KorgKodeAgent", "print_tool_schemas"]