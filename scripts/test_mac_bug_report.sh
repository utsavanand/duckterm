#!/usr/bin/env bash
# Native report-data regression checks; no running app or email account needed.
set -euo pipefail
cd "$(dirname "$0")/.."
REPORT_TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$REPORT_TEST_DIR"' EXIT
swiftc mac/Sources/Duckterm/BugReportData.swift mac/Tests/BugReportDataTests.swift \
  -o "$REPORT_TEST_DIR/report-data-tests"
"$REPORT_TEST_DIR/report-data-tests"
