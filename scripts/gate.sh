#!/usr/bin/env bash
# The full pre-release gate. Run it BARE — never pipe it through grep/tail
# (pipelines report the filter's exit code, and that shipped two broken
# releases; see RETRO.md). Output goes to a log; the exit code is the verdict.
#   scripts/gate.sh          # python + web + e2e
#   scripts/gate.sh --fast   # skip playwright e2e
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
LOG="${GATE_LOG:-$(mktemp /tmp/duckterm-gate.XXXXXX.log)}"
echo "Gate log: $LOG"
: > "$LOG"

step() { echo "==> $1"; }

step "dependency preflight"
"$PY" -c 'import pytest, ruff, black, mypy, duckterm.server' >> "$LOG" 2>&1
for tool in tsc eslint vitest playwright; do
  test -x "web/node_modules/.bin/$tool" || { echo "Missing $tool; run npm ci in web"; exit 1; }
done
step "pytest"
"$PY" -m pytest tests -q >> "$LOG" 2>&1
step "ruff check"
"$PY" -m ruff check src tests >> "$LOG" 2>&1
step "black --check"
"$PY" -m black --check src tests scripts >> "$LOG" 2>&1
step "mypy"
"$PY" -m mypy >> "$LOG" 2>&1
step "slop_check"
"$PY" scripts/slop_check.py >> "$LOG" 2>&1
step "ruff format --check"
"$PY" -m ruff format --check src tests >> "$LOG" 2>&1
cd web
step "tsc"
npx tsc --noEmit >> "$LOG" 2>&1
step "eslint"
npx eslint src e2e --max-warnings 0 >> "$LOG" 2>&1
step "vitest"
npx vitest run >> "$LOG" 2>&1
if [ "${1:-}" != "--fast" ]; then
  step "playwright e2e"
  PATH="$(cd .. && pwd)/.venv/bin:$PATH" npm run e2e >> "$LOG" 2>&1
fi
echo "==> GATE PASSED (log: $LOG)"
