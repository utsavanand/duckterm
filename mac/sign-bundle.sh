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
    codesign "${OPTIONS[@]}" --preserve-metadata=entitlements "$binary"
  fi
done < <(find "$APP/Contents" -type f -print0)
# Seal helper bundles inside-out after signing their executables. Sparkle's
# installer/XPC bundles must retain their own resource seals and entitlements.
while IFS= read -r -d '' nested; do
  codesign "${OPTIONS[@]}" --preserve-metadata=entitlements "$nested"
done < <(find "$APP/Contents" -depth -type d \( -name '*.xpc' -o -name '*.app' -o -name '*.framework' \) -print0)
codesign "${OPTIONS[@]}" "$APP"
codesign --verify --deep --strict "$APP"
