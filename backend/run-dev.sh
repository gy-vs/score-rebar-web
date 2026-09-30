#!/usr/bin/env bash
# Run backend dev server (auto-reload). Usage: ./backend/run-dev.sh
set -euo pipefail
cd "$(dirname "$0")"
export SCORE_REBAR_DATA="${SCORE_REBAR_DATA:-$(pwd)/data}"
exec python3 -m uvicorn app.main:app --reload --port "${PORT:-8000}"
