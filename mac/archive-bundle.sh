#!/usr/bin/env bash
# Package a signed app after notarization/stapling. Keep AppleDouble metadata
# outside its signed tree: Archive Utility/libarchive may leave inline ._* files.
set -euo pipefail

if [[ $# != 2 ]]; then
  echo "usage: mac/archive-bundle.sh APP.app OUTPUT.zip" >&2
  exit 2
fi

APP="$(cd "$1" && pwd -P)"
OUTPUT_PARENT="$(cd "$(dirname "$2")" && pwd -P)"
OUTPUT="$OUTPUT_PARENT/$(basename "$2")"
case "$APP" in *.app) ;; *) echo "Expected an app bundle" >&2; exit 2 ;; esac
case "$OUTPUT" in *.zip) ;; *) echo "Expected a ZIP output path" >&2; exit 2 ;; esac
case "$OUTPUT/" in "$APP/"*) echo "Archive must be outside the app" >&2; exit 2 ;; esac
if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
  echo "Refusing to overwrite $OUTPUT" >&2
  exit 1
fi
test -f "$APP/Contents/Info.plist"
/usr/bin/codesign --verify --deep --strict --all-architectures "$APP"

WORK="$(mktemp -d "$OUTPUT_PARENT/.duckterm-archive.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$WORK/archive.zip"

# Validate with a different extractor. Using ditto for both creation and
# validation previously hid sidecars left inside the signed Python runtime.
mkdir "$WORK/extracted"
/usr/bin/tar -xf "$WORK/archive.zip" -C "$WORK/extracted"
/usr/bin/codesign --verify --deep --strict --all-architectures \
  "$WORK/extracted/$(basename "$APP")"

# The staging file shares the output filesystem. link publishes atomically and
# refuses any existing destination, including a directory or dangling symlink.
/bin/link "$WORK/archive.zip" "$OUTPUT"
echo "$OUTPUT"
