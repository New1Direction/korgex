#!/usr/bin/env python3
"""Seluj CLI — the Jules clone command line interface."""

import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent import main

if __name__ == "__main__":
    main()