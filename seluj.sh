#!/usr/bin/env bash
# Seluj CLI entry point
cd "$(dirname "$0")"
python3 -m cli.main "$@"