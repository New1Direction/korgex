#!/usr/bin/env bash
# KorgKode CLI entry point
cd "$(dirname "$0")"
python3 -m cli.main "$@"