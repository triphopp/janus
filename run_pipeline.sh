#!/usr/bin/env bash
# run_pipeline.sh — thin wrapper around run_pipeline.py
#
# Usage:
#   ./run_pipeline.sh --instrument wti --start 2024-09-25 --end 2024-12-31
#   ./run_pipeline.sh --instrument wti --start 2024-09-25 --end 2024-12-31 \
#       --max-dte 90 --min-abs-delta 0.10 --max-abs-delta 0.90 --compute-greeks
#
# All args are forwarded verbatim to run_pipeline.py — run with --help to see all options.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$SCRIPT_DIR/run_pipeline.py" "$@"
