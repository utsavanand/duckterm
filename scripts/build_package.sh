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
python -m build

echo "==> done. Artifacts in dist/:"
ls -1 dist/
