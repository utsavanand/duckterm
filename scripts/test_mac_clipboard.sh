#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CLIPBOARD_TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$CLIPBOARD_TEST_DIR"' EXIT
swiftc -framework AppKit -framework ImageIO mac/Sources/Duckterm/ClipboardImage.swift mac/Tests/ClipboardImageTests.swift -o "$CLIPBOARD_TEST_DIR/clipboard-tests"
"$CLIPBOARD_TEST_DIR/clipboard-tests"
