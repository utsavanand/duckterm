#!/usr/bin/env bash
# Hooks use jq for structured events, approvals and turn-end messages.
set -euo pipefail
cd "$(dirname "$0")"
RESOURCES="$1"
mkdir -p "$RESOURCES"
RESOURCES="$(cd "$RESOURCES" && pwd)"
[[ "$(uname -m)" == arm64 ]] || { echo 'jq build requires arm64 macOS' >&2; exit 1; }
CACHE="${DUCKTERM_JQ_SOURCE_CACHE:-$PWD/build/jq-sources}"
mkdir -p "$CACHE" "$RESOURCES/jq/bin" "$RESOURCES/jq/licenses"
fetch() {
  local name="$1" sha="$2" actual
  if [[ ! -f "$CACHE/$name" ]]; then
    curl --fail --location --retry 3 "https://github.com/jqlang/jq/releases/download/jq-1.8.2/$name" -o "$CACHE/$name.download"
    mv "$CACHE/$name.download" "$CACHE/$name"
  fi
  actual="$(shasum -a 256 "$CACHE/$name")"
  [[ "${actual%% *}" == "$sha" ]] || { echo "jq checksum mismatch: $name" >&2; exit 1; }
}
fetch jq-1.8.2.tar.gz 71b8d6e8f5fe81f6c6d0d110e3892251f6ce76ed095abd315e26e6e1193af3af
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/duckterm-jq-build.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
tar -xzf "$CACHE/jq-1.8.2.tar.gz" -C "$BUILD"
# Upstream's arm64 executable targets macOS14. Build the same source for our
# macOS13 minimum, with Oniguruma linked statically and no Homebrew libraries.
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export MACOSX_DEPLOYMENT_TARGET=13.0
export SDKROOT="$(xcrun --show-sdk-path)"
export CC="$(xcrun --find clang)"
export CFLAGS='-O2 -arch arm64 -mmacosx-version-min=13.0'
export LDFLAGS='-arch arm64 -mmacosx-version-min=13.0'
unset CPPFLAGS CPATH LIBRARY_PATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH PKG_CONFIG_PATH
(
  cd "$BUILD/jq-1.8.2"
  ./configure --disable-docs --disable-maintainer-mode --with-oniguruma=builtin --disable-shared --enable-static
  make -j 4
)
cp "$BUILD/jq-1.8.2/jq" "$RESOURCES/jq/bin/jq"
chmod 755 "$RESOURCES/jq/bin/jq"
tar -xOf "$CACHE/jq-1.8.2.tar.gz" jq-1.8.2/COPYING > "$RESOURCES/jq/licenses/jq-COPYING"
tar -xOf "$CACHE/jq-1.8.2.tar.gz" jq-1.8.2/vendor/oniguruma/COPYING > "$RESOURCES/jq/licenses/oniguruma-COPYING"
"$RESOURCES/jq/bin/jq" --version
