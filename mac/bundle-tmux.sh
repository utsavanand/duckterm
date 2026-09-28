#!/usr/bin/env bash
# Reproducible Apple Silicon tmux: only macOS system dylibs remain dynamic.
set -euo pipefail
cd "$(dirname "$0")"
RESOURCES="$1"
mkdir -p "$RESOURCES"
RESOURCES="$(cd "$RESOURCES" && pwd)"
[[ "$(uname -m)" == arm64 ]] || { echo 'tmux build requires arm64 macOS' >&2; exit 1; }
CACHE="${DUCKTERM_TMUX_SOURCE_CACHE:-$PWD/build/tmux-sources}"
mkdir -p "$CACHE"
fetch() {
  local name="$1" sha="$2" url="$3" actual
  if [[ ! -f "$CACHE/$name" ]]; then
    curl --fail --location --retry 3 "$url" -o "$CACHE/$name.download"
    mv "$CACHE/$name.download" "$CACHE/$name"
  fi
  actual="$(shasum -a 256 "$CACHE/$name")"
  [[ "${actual%% *}" == "$sha" ]] || { echo "tmux source checksum mismatch: $name" >&2; exit 1; }
}
fetch tmux-3.7c.tar.gz 7c60cae9a0e25288e2e24750aafc9e8800fc7fd4555e447e1b29ee4201cfb3bf https://github.com/tmux/tmux/releases/download/3.7c/tmux-3.7c.tar.gz
fetch libevent-2.1.13-stable.tar.gz f7e9383b8c0baa81b687e5b5eecc01beefaf1b19b64151d95ed61647fe7a315c https://github.com/libevent/libevent/releases/download/release-2.1.13-stable/libevent-2.1.13-stable.tar.gz
fetch utf8proc-2.12.0.tar.gz a393fbef160835fb315bc3e91ba8d86f7a73a7cec9e6198b6c60b848b498bfeb https://github.com/JuliaStrings/utf8proc/releases/download/v2.12.0/utf8proc-2.12.0.tar.gz
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/duckterm-tmux-build.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
for source in tmux-3.7c libevent-2.1.13-stable utf8proc-2.12.0; do
  tar -xzf "$CACHE/$source.tar.gz" -C "$BUILD"
done
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export MACOSX_DEPLOYMENT_TARGET=13.0
export SDKROOT="$(xcrun --show-sdk-path)"
export CC="$(xcrun --find clang)"
export CFLAGS='-O2 -arch arm64 -mmacosx-version-min=13.0'
export CPPFLAGS="-I$BUILD/prefix/include"
export LDFLAGS="-arch arm64 -mmacosx-version-min=13.0 -L$BUILD/prefix/lib"
export PKG_CONFIG=/usr/bin/false
unset CPATH LIBRARY_PATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH PKG_CONFIG_PATH
(
  cd "$BUILD/libevent-2.1.13-stable"
  ./configure --prefix="$BUILD/prefix" --disable-shared --enable-static --disable-openssl --disable-samples --disable-libevent-regress
  make -j 4
  make install
)
(
  cd "$BUILD/utf8proc-2.12.0"
  make -j 4 libutf8proc.a
  cp utf8proc.h "$BUILD/prefix/include/"
  cp libutf8proc.a "$BUILD/prefix/lib/"
)
(
  cd "$BUILD/tmux-3.7c"
  LIBEVENT_CORE_CFLAGS="-I$BUILD/prefix/include" LIBEVENT_CORE_LIBS="$BUILD/prefix/lib/libevent_core.a" \
  LIBUTF8PROC_CFLAGS="-I$BUILD/prefix/include" LIBUTF8PROC_LIBS="$BUILD/prefix/lib/libutf8proc.a" \
    ./configure --prefix=/usr --enable-utf8proc --disable-systemd --disable-jemalloc
  make -j 4
)
mkdir -p "$RESOURCES/tmux/bin" "$RESOURCES/tmux/licenses"
cp "$BUILD/tmux-3.7c/tmux" "$RESOURCES/tmux/bin/tmux"
cp "$BUILD/tmux-3.7c/COPYING" "$RESOURCES/tmux/licenses/tmux-COPYING"
cp "$BUILD/libevent-2.1.13-stable/LICENSE" "$RESOURCES/tmux/licenses/libevent-LICENSE"
cp "$BUILD/utf8proc-2.12.0/LICENSE.md" "$RESOURCES/tmux/licenses/utf8proc-LICENSE.md"
chmod 755 "$RESOURCES/tmux/bin/tmux"
# Reject accidental links to build-machine packages rather than rewriting guesses.
otool -L "$RESOURCES/tmux/bin/tmux" > "$BUILD/links"
awk 'NR > 1 && $1 !~ /^\/usr\/lib\// && $1 !~ /^\/System\/Library\// { bad=1; print } END { exit bad }' "$BUILD/links"
codesign --force --sign - "$RESOURCES/tmux/bin/tmux"
"$RESOURCES/tmux/bin/tmux" -V
