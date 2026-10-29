#!/usr/bin/env bash
# Pinned CPython distribution from Astral's python-build-standalone project.
# The archive contains its runtime dependencies and third-party licenses.
set -euo pipefail
cd "$(dirname "$0")"
RESOURCES="$1"
[[ "$(uname -m)" == arm64 ]] || { echo 'Python bundle requires arm64 macOS' >&2; exit 1; }
ARCHIVE="${DUCKTERM_PYTHON_ARCHIVE:-build/python-3.13.18-arm64.tar.gz}"
SHA256=8392b6ea51d94391befb2c31ebe75463f045d0b69dfcf5e1bb3234e1f5fa3405
URL='https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.13.18%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz'
METADATA_ARCHIVE="${DUCKTERM_PYTHON_METADATA_ARCHIVE:-build/python-3.13.18-full.tar.zst}"
METADATA_SHA256=5706f1fdf82f7ea10b56a9b9cbadfa5f372010568a9794f19e0af4c48d975f34
METADATA_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.13.18%2B20261026-aarch64-apple-darwin-pgo%2Blto-full.tar.zst'
fetch() {
  local archive="$1" sha="$2" url="$3" actual
  if [[ ! -f "$archive" ]]; then
    mkdir -p "$(dirname "$archive")"
    curl --fail --location --retry 3 "$url" -o "$archive.download"
    mv "$archive.download" "$archive"
  fi
  actual="$(shasum -a 256 "$archive")"
  [[ "${actual%% *}" == "$sha" ]] || { echo 'Bundled Python checksum mismatch' >&2; exit 1; }
}
fetch "$ARCHIVE" "$SHA256" "$URL"
fetch "$METADATA_ARCHIVE" "$METADATA_SHA256" "$METADATA_URL"
mkdir -p "$RESOURCES"
tar -xzf "$ARCHIVE" -C "$RESOURCES"
# install_only omits the dependency notices; retain the matching full build's
# licenses and component metadata without shipping its compiler objects.
tar -xf "$METADATA_ARCHIVE" -C "$RESOURCES/python" --strip-components=1 python/licenses python/PYTHON.json
mkdir -p "$RESOURCES/bin"
cp Resources/duckterm-cli "$RESOURCES/bin/duckterm"
chmod 755 "$RESOURCES/bin/duckterm" "$RESOURCES/python/bin/python3.13"
cat > "$RESOURCES/python-distribution.json" <<EOF
{"version":"3.13.18","publisher":"astral-sh/python-build-standalone","release":"20261026","sha256":"$SHA256","url":"$URL","metadata_sha256":"$METADATA_SHA256","metadata_url":"$METADATA_URL"}
EOF
