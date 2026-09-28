#!/usr/bin/env bash
# Pinned official python-build-standalone install-only archive (Apple Silicon).
# https://github.com/astral-sh/python-build-standalone/releases/tag/20260924
set -euo pipefail
cd "$(dirname "$0")"
RESOURCES="$1"
if [[ "$(uname -m)" != arm64 ]]; then
  echo "This build targets Apple Silicon; build it on an arm64 Mac." >&2
  exit 1
fi
ARCHIVE="${DUCKTERM_PYTHON_ARCHIVE:-build/python-3.13.15-arm64.tar.gz}"
SHA256=064afb7c2fc0bbf511d886288adf98696af5105e36c138cdf2c199c0146fcf68
URL='https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.13.15%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'
if [[ ! -f "$ARCHIVE" ]]; then
  mkdir -p "$(dirname "$ARCHIVE")"
  curl --fail --location --retry 3 "$URL" -o "$ARCHIVE.download"
  mv "$ARCHIVE.download" "$ARCHIVE"
fi
ACTUAL="$(shasum -a 256 "$ARCHIVE")"
[[ "${ACTUAL%% *}" == "$SHA256" ]] || { echo "Bundled Python checksum mismatch" >&2; exit 1; }
mkdir -p "$RESOURCES"
tar -xzf "$ARCHIVE" -C "$RESOURCES"
mkdir -p "$RESOURCES/bin"
cp Resources/duckterm-cli "$RESOURCES/bin/duckterm"
chmod 755 "$RESOURCES/bin/duckterm" "$RESOURCES/python/bin/python3.13"
