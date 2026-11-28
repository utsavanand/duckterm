#!/usr/bin/env bash
# Native WKWebView checks. Opens temporary test windows, never sends email.
set -euo pipefail
cd "$(dirname "$0")/.."
REPORT_UI_TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$REPORT_UI_TEST_DIR"' EXIT
mkdir -p "$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/MacOS"
cat > "$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.rubberduckhq.report-ui-tests</string><key>CFBundleExecutable</key><string>report-ui-tests</string><key>CFBundlePackageType</key><string>APPL</string></dict></plist>
PLIST
sources=()
for source in mac/Sources/Duckterm/*.swift; do
  [[ "$source" == */main.swift ]] || sources+=("$source")
done
REPORT_SPARKLE_DIR="$(pwd)/mac/.build/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64"
if [[ ! -d "$REPORT_SPARKLE_DIR/Sparkle.framework" ]]; then
  echo "Resolve the pinned Mac dependencies first: cd mac && swift package resolve" >&2
  exit 1
fi
swiftc -framework AppKit -framework WebKit -F "$REPORT_SPARKLE_DIR" -framework Sparkle \
  -Xlinker -rpath -Xlinker "$REPORT_SPARKLE_DIR" \
  "${sources[@]}" mac/Tests/BugReportUITests.swift \
  -o "$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/MacOS/report-ui-tests"
"$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/MacOS/report-ui-tests"
