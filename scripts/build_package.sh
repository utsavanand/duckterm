#!/bin/bash
# Build a publishable package: rebuild the dashboard, bundle it into the
# Python package, then build the sdist + wheel. Run before publishing.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> building dashboard"
(cd web && npm ci --silent && npm run build --silent)

echo "==> bundling dashboard into the package"
rm -rf src/duckterm/dashboard
mkdir -p src/duckterm/dashboard
cp -r web/dist/. src/duckterm/dashboard/

echo "==> building sdist + wheel"
rm -rf dist build ./*.egg-info
# The repo venv has `build` installed; a bare `python` only exists inside an
# activated venv, so this failed when run from a plain shell.
"${PYTHON:-.venv/bin/python}" -m build

echo "==> smoke check: the wheel's server module must import"
# A hunk-filtered commit once shipped an import whose module stayed
# uncommitted — the installed server crashed at startup. Import the server
# from the built wheel in a scratch venv so a broken wheel can't be released.
SMOKE=$(mktemp -d)
"${PYTHON:-.venv/bin/python}" -m venv "$SMOKE/venv"
"$SMOKE/venv/bin/pip" -q install dist/duckterm-*.whl
"$SMOKE/venv/bin/python" -c "import duckterm.server, duckterm.cli"
rm -rf "$SMOKE"

echo "==> done. Artifacts in dist/:"
ls -1 dist/
