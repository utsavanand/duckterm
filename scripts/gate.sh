#!/usr/bin/env bash
# The full pre-release gate. Run it BARE — never pipe it through grep/tail
# (pipelines report the filter's exit code, and that shipped two broken
# releases; see RETRO.md). Output goes to a log; the exit code is the verdict.
#   scripts/gate.sh          # python + web + e2e
#   scripts/gate.sh --fast   # skip playwright e2e
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
LOG="${GATE_LOG:-/tmp/duckterm-gate.log}"
: > "$LOG"

step() { echo "==> $1"; }

step "pytest"
"$PY" -m pytest tests -q >> "$LOG" 2>&1
step "ruff check"
"$PY" -m ruff check src tests >> "$LOG" 2>&1
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
  PATH="$(cd .. && pwd)/.venv/bin:$PATH" npx playwright test >> "$LOG" 2>&1
fi
echo "==> GATE PASSED (log: $LOG)"
