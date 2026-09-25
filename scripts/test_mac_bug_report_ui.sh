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
swiftc -framework AppKit -framework WebKit \
  mac/Sources/Duckterm/BugReportData.swift mac/Sources/Duckterm/BugReport.swift \
  mac/Sources/Duckterm/AppIdentity.swift mac/Sources/Duckterm/RemoteHost.swift \
  mac/Sources/Duckterm/DashboardWindow.swift mac/Tests/BugReportUITests.swift \
  -o "$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/MacOS/report-ui-tests"
"$REPORT_UI_TEST_DIR/ReportUITests.app/Contents/MacOS/report-ui-tests"
