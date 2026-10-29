#!/usr/bin/env bash
# Sign nested native code first, then seal the enclosing app. Distribution
# callers supply a Developer ID identity; local builds use ad-hoc signing.
set -euo pipefail
APP="$1"
IDENTITY="${2:--}"
OPTIONS=(--force --sign "$IDENTITY")
if [[ "$IDENTITY" != - ]]; then
  OPTIONS+=(--options runtime --timestamp)
fi
while IFS= read -r -d '' binary; do
  if /usr/bin/file -b "$binary" | /usr/bin/grep -q 'Mach-O'; then
    codesign "${OPTIONS[@]}" "$binary"
  fi
done < <(find "$APP/Contents" -type f -print0)
codesign "${OPTIONS[@]}" "$APP"
codesign --verify --deep --strict "$APP"
